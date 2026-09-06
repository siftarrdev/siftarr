"""Observe approved qBittorrent downloads without mutating qBittorrent."""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, Literal, cast
from urllib.parse import quote

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.siftarr.config import get_settings
from app.siftarr.models.activity_log import ActivityLog, EventType
from app.siftarr.models.download_health import (
    HEALTH_PROBLEM_STATUSES,
    HEALTH_STATUS_AMBIGUOUS,
    HEALTH_STATUS_HEALTHY,
    HEALTH_STATUS_MISSING,
    HEALTH_STATUS_MONITORING,
    HEALTH_STATUS_RECOVERED,
    HEALTH_STATUS_RECOVERY_ELIGIBLE,
    HEALTH_STATUS_SUSPENDED,
    HEALTH_STATUS_WARNING,
    DownloadHealth,
)
from app.siftarr.models.request import (
    RESETTABLE_EPISODE_DOWNLOAD_STATUSES,
    Request,
    RequestStatus,
    is_terminal_request_status,
)
from app.siftarr.models.season import Season
from app.siftarr.models.staged_torrent import StagedTorrent
from app.siftarr.services.integrations.qbittorrent_service import QbittorrentService
from app.siftarr.services.lifecycle.episode_derive import (
    derive_request_status_from_episodes,
    derive_season_status,
)
from app.siftarr.services.releases.release_disposition_service import ReleaseDispositionService
from app.siftarr.services.releases.release_parser import cached_parse_release_coverage
from app.siftarr.services.utils.torrent_identity import normalize_torrent_name

logger = logging.getLogger(__name__)

RecoveryDisposition = Literal["cooldown", "permanent_rejection"]

HEALTH_EVENT_WARNING = "download_health_warning"
HEALTH_EVENT_RECOVERY_ELIGIBLE = "download_health_recovery_eligible"
HEALTH_EVENT_MISSING = "download_health_missing"
HEALTH_EVENT_RECOVERED = "download_health_recovered"

# qBittorrent states in these groups do not represent useful download work.
# Time spent there is accumulated separately and removed when a torrent resumes.
_SUSPENDED_STATE_PARTS = ("paused", "queued", "checking", "stopped")
_SEED_STATES = {
    "uploading",
    "stalledup",
    "forcedup",
    "queuedup",
    "checkingup",
    "seeding",
}


class DownloadHealthError(RuntimeError):
    """Base class for operator-facing health action errors."""


class DownloadHealthNotFound(DownloadHealthError):
    """The requested health attempt does not exist."""


class DownloadRecoveryNotEligible(DownloadHealthError):
    """A replacement cannot be confirmed for the current health state."""


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _iso(value: datetime | None) -> str | None:
    normalized = _as_utc(value)
    return normalized.isoformat() if normalized is not None else None


def _qbit_time(value: object) -> datetime | None:
    """Convert qBittorrent epoch seconds to UTC, rejecting junk values."""
    if isinstance(value, datetime):
        return _as_utc(value)
    if isinstance(value, str):
        try:
            value = float(value)
        except ValueError:
            return None
    if isinstance(value, int | float) and value > 0:
        try:
            return datetime.fromtimestamp(float(value), tz=UTC)
        except OverflowError, OSError, ValueError:
            return None
    return None


def _progress(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float | str):
        try:
            parsed = float(value)
        except ValueError:
            return None
        return parsed if 0 <= parsed <= 1 else None
    return None


def _state(value: object) -> str:
    return str(value or "").strip().casefold()


def _is_suspended_state(state: str) -> bool:
    return any(part in state for part in _SUSPENDED_STATE_PARTS) or state in _SEED_STATES


def _is_seed_state(state: str, progress: float | None) -> bool:
    return state in _SEED_STATES or (progress is not None and progress >= 1.0)


async def _add_health_event(
    db: AsyncSession,
    attempt: DownloadHealth,
    *,
    event_type: str,
    now: datetime,
    reason: str,
) -> None:
    """Persist one threshold transition without extending the global enum."""
    details = {
        "health_id": attempt.id,
        "staged_torrent_id": attempt.staged_torrent_id,
        "request_id": attempt.request_id,
        "title": attempt.title,
        "status": attempt.status,
        "reason": reason,
        "observed_at": now.isoformat(),
        "progress": attempt.last_progress,
        "state": attempt.last_state,
    }
    db.add(
        ActivityLog(
            request_id=attempt.request_id,
            event_type=event_type,
            details=json.dumps(details, default=str),
            created_at=now,
        )
    )
    await db.flush()


class DownloadHealthService:
    """Track stalled approved downloads and offer explicitly confirmed recovery."""

    def __init__(self, db: AsyncSession, qbittorrent: QbittorrentService | None = None) -> None:
        self.db = db
        self.qbittorrent = qbittorrent

    async def _load_approved_rows(
        self,
    ) -> list[tuple[StagedTorrent, Request | None, DownloadHealth | None]]:
        result = await self.db.execute(
            select(StagedTorrent, Request, DownloadHealth)
            .outerjoin(Request, Request.id == StagedTorrent.request_id)
            .outerjoin(DownloadHealth, DownloadHealth.staged_torrent_id == StagedTorrent.id)
            .where(StagedTorrent.status == "approved")
        )
        return [
            cast(tuple[StagedTorrent, Request | None, DownloadHealth | None], row)
            for row in result.all()
        ]

    async def _historical_start_times(
        self,
        rows: list[tuple[StagedTorrent, Request | None, DownloadHealth | None]],
    ) -> dict[int, datetime]:
        """Backfill attempts from existing staged DOWNLOAD_STARTED activity."""
        try:
            result = await self.db.execute(
                select(ActivityLog).where(
                    ActivityLog.event_type == EventType.DOWNLOAD_STARTED.value
                )
            )
            entries = list(result.scalars().all())
        except Exception:
            logger.debug("Unable to read historical download activity", exc_info=True)
            return {}

        starts: dict[int, datetime] = {}
        by_release_id = {
            staged.source_release_id: staged.id
            for staged, _request, _attempt in rows
            if staged.source_release_id is not None
        }
        by_request_title = {
            (staged.request_id, normalize_torrent_name(staged.title)): staged.id
            for staged, _request, _attempt in rows
            if staged.request_id is not None
        }
        for entry in entries:
            try:
                details = json.loads(entry.details or "{}")
            except TypeError, ValueError:
                continue
            if not isinstance(details, dict):
                continue
            created = _as_utc(entry.created_at)
            if created is None:
                continue
            staged_id = details.get("torrent_id")
            if not isinstance(staged_id, int):
                release_id = details.get("release_id")
                staged_id = by_release_id.get(release_id)
            if not isinstance(staged_id, int):
                title = details.get("title")
                if isinstance(entry.request_id, int) and isinstance(title, str):
                    staged_id = by_request_title.get(
                        (entry.request_id, normalize_torrent_name(title))
                    )
            if isinstance(staged_id, int):
                starts[staged_id] = min(starts.get(staged_id, created), created)
        return starts

    async def _ensure_attempts(
        self,
        rows: list[tuple[StagedTorrent, Request | None, DownloadHealth | None]],
        *,
        now: datetime,
    ) -> list[tuple[StagedTorrent, Request | None, DownloadHealth]]:
        historical = await self._historical_start_times(rows) if rows else {}
        ensured: list[tuple[StagedTorrent, Request | None, DownloadHealth]] = []
        for staged, request, attempt in rows:
            if request is not None and is_terminal_request_status(request.status):
                continue
            if attempt is None:
                first_observed = historical.get(staged.id) or _as_utc(staged.created_at) or now
                attempt = DownloadHealth(
                    staged_torrent_id=staged.id,
                    request_id=staged.request_id,
                    title=staged.title,
                    info_hash=staged.info_hash.lower() if staged.info_hash else None,
                    coverage_scope=staged.target_scope,
                    first_observed_at=first_observed,
                )
                self.db.add(attempt)
                await self.db.flush()
            elif (
                isinstance(attempt.info_hash, str) and attempt.info_hash.strip().casefold() == "ok."
            ):
                attempt.info_hash = staged.info_hash.lower() if staged.info_hash else None
            elif not attempt.info_hash and staged.info_hash:
                attempt.info_hash = staged.info_hash.lower()
            ensured.append((staged, request, attempt))
        return ensured

    @staticmethod
    def _torrent_maps(
        torrents: list[dict[str, Any]],
    ) -> tuple[dict[str, dict], dict[str, list[dict]]]:
        by_hash: dict[str, dict] = {}
        by_name: dict[str, list[dict]] = {}
        for torrent in torrents:
            torrent_hash = torrent.get("hash")
            if isinstance(torrent_hash, str) and torrent_hash.strip():
                by_hash[torrent_hash.casefold()] = torrent
            name = torrent.get("name")
            if isinstance(name, str) and name.strip():
                by_name.setdefault(normalize_torrent_name(name), []).append(torrent)
        return by_hash, by_name

    @staticmethod
    def _match_torrent(
        staged: StagedTorrent,
        attempt: DownloadHealth,
        by_hash: dict[str, dict],
        by_name: dict[str, list[dict]],
    ) -> tuple[dict[str, Any] | None, bool]:
        known_hash = attempt.info_hash or staged.info_hash
        if isinstance(known_hash, str) and known_hash.strip().casefold() == "ok.":
            # Older direct submissions could accidentally persist qBit's
            # accepted-but-not-a-hash response.  Fall back to the unique title
            # matcher until qBit exposes the real hash.
            known_hash = None
        if isinstance(known_hash, str) and known_hash.strip():
            return by_hash.get(known_hash.casefold()), False
        candidates = by_name.get(normalize_torrent_name(staged.title), [])
        if len(candidates) == 1:
            return candidates[0], False
        return None, len(candidates) > 1

    @staticmethod
    def _stall_age_seconds(attempt: DownloadHealth, now: datetime) -> float | None:
        anchor = _as_utc(attempt.last_progress_at or attempt.progress_started_at)
        if anchor is None:
            return None
        return max(
            0.0,
            (now - anchor).total_seconds() - max(float(attempt.suspended_seconds or 0), 0),
        )

    async def _observe_missing(
        self, attempt: DownloadHealth, *, now: datetime, ambiguous: bool = False
    ) -> dict[str, object]:
        old_status = attempt.status
        attempt.last_observed_at = now
        attempt.last_observation = {"qbit_available": True, "found": False}
        if ambiguous:
            attempt.status = HEALTH_STATUS_AMBIGUOUS
            attempt.failure_reason = "Multiple qBittorrent torrents share the title"
        else:
            attempt.missing_since = attempt.missing_since or now
            attempt.status = HEALTH_STATUS_MISSING
            attempt.failure_reason = "Torrent was not found in qBittorrent"
        if attempt.status != old_status:
            await _add_health_event(
                self.db,
                attempt,
                event_type=HEALTH_EVENT_MISSING,
                now=now,
                reason="ambiguous_title" if ambiguous else "missing_from_qbittorrent",
            )
        return self._serialize_attempt(attempt, now=now)

    async def _observe_torrent(
        self,
        staged: StagedTorrent,
        attempt: DownloadHealth,
        torrent: dict[str, Any],
        *,
        now: datetime,
    ) -> dict[str, object]:
        old_status = attempt.status
        prior_progress = _progress(attempt.last_progress)
        was_unobserved = attempt.last_observed_at is None
        attempt.last_observed_at = now
        attempt.last_seen_at = now
        attempt.missing_since = None
        attempt.failure_reason = None

        torrent_hash = torrent.get("hash")
        if not attempt.info_hash and isinstance(torrent_hash, str) and torrent_hash.strip():
            attempt.info_hash = torrent_hash.strip().lower()
        progress = _progress(torrent.get("progress"))
        state = _state(torrent.get("state"))
        attempt.last_progress = progress
        attempt.last_state = str(torrent.get("state") or "") or None
        attempt.qbit_added_on = _qbit_time(torrent.get("added_on"))
        attempt.qbit_last_activity = _qbit_time(torrent.get("last_activity"))
        attempt.qbit_completed_on = _qbit_time(torrent.get("completed_on"))
        attempt.last_observation = {
            "qbit_available": True,
            "found": True,
            "hash": attempt.info_hash,
            "name": torrent.get("name"),
            "progress": progress,
            "state": torrent.get("state"),
        }

        if attempt.suspended_since is not None and not _is_suspended_state(state):
            suspended_since = _as_utc(attempt.suspended_since)
            if suspended_since is not None:
                attempt.suspended_seconds = float(attempt.suspended_seconds or 0) + max(
                    0, (now - suspended_since).total_seconds()
                )
            attempt.suspended_since = None

        if progress is not None:
            if prior_progress is None:
                if progress == 0:
                    anchors = [
                        value
                        for value in (attempt.qbit_added_on, attempt.qbit_last_activity)
                        if value is not None
                    ]
                    anchor = (
                        now
                        if was_unobserved and _is_suspended_state(state)
                        else max(anchors, default=None)
                    )
                    attempt.progress_started_at = anchor or now
                    attempt.last_progress_at = anchor or now
                else:
                    # A partial first observation has no trustworthy stall age.
                    attempt.progress_started_at = now
                    attempt.last_progress_at = now
            elif progress > prior_progress:
                attempt.last_progress_at = now
                attempt.progress_started_at = attempt.progress_started_at or now

        if _is_seed_state(state, progress):
            attempt.status = HEALTH_STATUS_HEALTHY
            if progress is not None and progress >= 1.0:
                attempt.completed_at = attempt.completed_at or now
            return self._serialize_attempt(attempt, now=now)

        if _is_suspended_state(state):
            attempt.suspended_since = attempt.suspended_since or now
            attempt.status = HEALTH_STATUS_SUSPENDED
            return self._serialize_attempt(attempt, now=now)

        age_seconds = self._stall_age_seconds(attempt, now)
        if age_seconds is None:
            attempt.progress_started_at = now
            attempt.last_progress_at = now
            age_seconds = 0.0

        settings = get_settings()
        warning_seconds = max(int(settings.download_stall_warning_hours), 1) * 3600
        recovery_seconds = max(int(settings.download_stall_recovery_hours), 1) * 3600
        if age_seconds >= recovery_seconds:
            attempt.status = HEALTH_STATUS_RECOVERY_ELIGIBLE
            attempt.recovery_eligible_at = attempt.recovery_eligible_at or now
            if old_status != HEALTH_STATUS_RECOVERY_ELIGIBLE:
                await _add_health_event(
                    self.db,
                    attempt,
                    event_type=HEALTH_EVENT_RECOVERY_ELIGIBLE,
                    now=now,
                    reason="stall_recovery_threshold",
                )
        elif age_seconds >= warning_seconds:
            attempt.status = HEALTH_STATUS_WARNING
            attempt.warning_at = attempt.warning_at or now
            if old_status != HEALTH_STATUS_WARNING:
                await _add_health_event(
                    self.db,
                    attempt,
                    event_type=HEALTH_EVENT_WARNING,
                    now=now,
                    reason="stall_warning_threshold",
                )
        else:
            attempt.status = HEALTH_STATUS_MONITORING

        return self._serialize_attempt(attempt, now=now)

    def _serialize_attempt(self, attempt: DownloadHealth, *, now: datetime) -> dict[str, object]:
        age_seconds = self._stall_age_seconds(attempt, now)
        if attempt.status == HEALTH_STATUS_MISSING and attempt.missing_since:
            missing_since = _as_utc(attempt.missing_since)
            age_seconds = max(0, (now - missing_since).total_seconds()) if missing_since else None
        return {
            "id": attempt.id,
            "staged_torrent_id": attempt.staged_torrent_id,
            "request_id": attempt.request_id,
            "title": attempt.title,
            "info_hash": attempt.info_hash,
            "coverage_scope": attempt.coverage_scope,
            "status": attempt.status,
            "progress": attempt.last_progress,
            "qbit_state": attempt.last_state,
            "qbit_added_on": _iso(attempt.qbit_added_on),
            "qbit_last_activity": _iso(attempt.qbit_last_activity),
            "qbit_completed_on": _iso(attempt.qbit_completed_on),
            "failure_reason": attempt.failure_reason,
            "recovery_disposition": attempt.recovery_disposition,
            "cooldown_until": _iso(attempt.cooldown_until),
            "age_hours": round(age_seconds / 3600, 3) if age_seconds is not None else None,
            "first_observed_at": (_iso(attempt.first_observed_at)),
            "last_observed_at": (_iso(attempt.last_observed_at)),
            "warning_at": _iso(attempt.warning_at),
            "recovery_eligible_at": (_iso(attempt.recovery_eligible_at)),
            "replacement_available": attempt.status
            in {HEALTH_STATUS_RECOVERY_ELIGIBLE, HEALTH_STATUS_MISSING},
            "original_remains_in_qbit": True,
        }

    async def observe(
        self, now: datetime | None = None, *, fetch_when_empty: bool = False
    ) -> dict[str, object]:
        """Observe all approved attempts and persist current state transitions."""
        observed_at = _as_utc(now) or datetime.now(UTC)
        rows = await self._load_approved_rows()
        attempts = await self._ensure_attempts(rows, now=observed_at)
        if not attempts and not fetch_when_empty:
            return {"qbit_available": True, "observed_at": observed_at.isoformat(), "problems": []}

        qbittorrent = self.qbittorrent or QbittorrentService(settings=get_settings())
        try:
            torrents = await qbittorrent.get_all_active_torrents_or_raise()
        except Exception as exc:
            logger.warning("Download health observation skipped: qBittorrent unavailable: %s", exc)
            return {
                "qbit_available": False,
                "observed_at": observed_at.isoformat(),
                "problems": await self.list_problems(now=observed_at),
            }

        by_hash, by_name = self._torrent_maps(list(torrents or []))
        serialized: list[dict[str, object]] = []
        for staged, _request, attempt in attempts:
            torrent, ambiguous = self._match_torrent(staged, attempt, by_hash, by_name)
            if torrent is None:
                serialized.append(
                    await self._observe_missing(attempt, now=observed_at, ambiguous=ambiguous)
                )
            else:
                serialized.append(
                    await self._observe_torrent(staged, attempt, torrent, now=observed_at)
                )
        await self.db.flush()
        return {
            "qbit_available": True,
            "observed_at": observed_at.isoformat(),
            "updated": len(serialized),
            "problems": [item for item in serialized if item["status"] in HEALTH_PROBLEM_STATUSES],
        }

    async def list_problems(self, *, now: datetime | None = None) -> list[dict[str, object]]:
        """Return persisted problems without polling qBittorrent."""
        observed_at = _as_utc(now) or datetime.now(UTC)
        result = await self.db.execute(
            select(DownloadHealth)
            .where(DownloadHealth.status.in_(HEALTH_PROBLEM_STATUSES))
            .order_by(DownloadHealth.recovery_eligible_at.desc(), DownloadHealth.warning_at.desc())
        )
        return [self._serialize_attempt(row, now=observed_at) for row in result.scalars().all()]

    async def _record_disposition(
        self,
        attempt: DownloadHealth,
        staged: StagedTorrent,
        request: Request | None,
        *,
        disposition: RecoveryDisposition,
        now: datetime,
    ) -> datetime | None:
        if request is None:
            return None
        release = SimpleNamespace(
            info_hash=attempt.info_hash or staged.info_hash,
            title=staged.title,
            size=staged.size,
            indexer=staged.indexer,
        )
        service = ReleaseDispositionService(self.db)
        cooldown_until = (
            now + timedelta(hours=max(int(get_settings().release_failure_cooldown_hours), 1))
            if disposition == "cooldown"
            else None
        )
        recorded = await service.record_rejection(
            request.id,
            release,
            media_type=request.media_type,
            reason="confirmed_download_health_recovery",
            staged_torrent_id=staged.id,
        )
        recorded.kind = "cooldown" if disposition == "cooldown" else "rejected"
        recorded.expires_at = cooldown_until
        return cooldown_until

    @staticmethod
    def _episode_numbers_from_title(title: str, season_number: int) -> set[int]:
        """Return explicit multi-episode numbers from an episode release title."""
        match = re.search(r"\bS(?P<season>\d{1,2})E(?P<episode>\d{1,3})", title, re.I)
        if match is None or int(match.group("season")) != season_number:
            return set()
        numbers = {int(match.group("episode"))}
        tail = title[match.end() :]
        tail_match = re.match(r"(?:(?:E\d{1,3}|-\s*E?\d{1,3}))+", tail, re.I)
        if tail_match is None:
            return numbers
        previous = numbers.copy()
        for token in re.finditer(
            r"(?P<range>-\s*E?|E)(?P<number>\d{1,3})", tail_match.group(0), re.I
        ):
            number = int(token.group("number"))
            if token.group("range").lstrip().startswith("-"):
                numbers.update(range(min(previous), number + 1))
            else:
                numbers.add(number)
            previous = numbers.copy()
        return numbers

    @classmethod
    def _covered_episode_numbers(
        cls, title: str, season_number: int, requested_numbers: set[int]
    ) -> set[int]:
        """Return the requested episodes covered by a staged release."""
        coverage = cached_parse_release_coverage(title)
        if coverage.is_complete_series or (
            coverage.episode_number is None and season_number in coverage.season_numbers
        ):
            return requested_numbers.copy()
        if coverage.episode_number is None or coverage.season_number != season_number:
            return set()
        return cls._episode_numbers_from_title(title, season_number).intersection(requested_numbers)

    @classmethod
    def _reset_request_scope(cls, request: Request, title: str) -> dict[str, object]:
        if str(getattr(request.media_type, "value", request.media_type)) != "tv":
            if not is_terminal_request_status(request.status):
                request.status = RequestStatus.PENDING
            request.next_retry_at = None
            request.retry_count = 0
            request.rejection_reason = None
            return {"media_type": "movie", "title": title}

        affected: list[str] = []
        for season in request.seasons:
            requested_numbers = {episode.episode_number for episode in season.episodes}
            covered_numbers = cls._covered_episode_numbers(
                title, season.season_number, requested_numbers
            )
            for episode in season.episodes:
                if (
                    episode.episode_number in covered_numbers
                    and episode.status in RESETTABLE_EPISODE_DOWNLOAD_STATUSES
                ):
                    episode.status = RequestStatus.PENDING
                    affected.append(f"S{season.season_number:02d}E{episode.episode_number:02d}")
            season.status = derive_season_status(list(season.episodes))
        request.status = derive_request_status_from_episodes(
            [episode for season in request.seasons for episode in season.episodes]
        )
        request.next_retry_at = None
        request.retry_count = 0
        request.rejection_reason = None
        return {"media_type": "tv", "title": title, "episodes": affected}

    async def recover(
        self,
        attempt_id: int,
        *,
        confirm: bool,
        disposition: RecoveryDisposition = "cooldown",
        now: datetime | None = None,
    ) -> dict[str, object]:
        """Confirm replacement review, leaving the original torrent/files intact."""
        if not confirm:
            raise DownloadRecoveryNotEligible("Explicit confirmation is required")
        if disposition not in {"cooldown", "permanent_rejection"}:
            raise DownloadRecoveryNotEligible("Unknown recovery disposition")
        observed_at = _as_utc(now) or datetime.now(UTC)
        result = await self.db.execute(
            select(DownloadHealth).where(DownloadHealth.id == attempt_id)
        )
        attempt = result.scalar_one_or_none()
        if attempt is None:
            raise DownloadHealthNotFound(f"Download health attempt {attempt_id} was not found")
        qbittorrent = self.qbittorrent or QbittorrentService(settings=get_settings())
        observation_service = (
            self if self.qbittorrent is not None else DownloadHealthService(self.db, qbittorrent)
        )
        observation = await observation_service.observe(observed_at, fetch_when_empty=True)
        if not observation.get("qbit_available", False):
            raise DownloadRecoveryNotEligible(
                "qBittorrent is unavailable; recovery was not confirmed"
            )

        # Reload after the strict observation.  The saved status is only a
        # prompt for review, never proof that the torrent is still stalled.
        result = await self.db.execute(
            select(DownloadHealth).where(DownloadHealth.id == attempt_id)
        )
        attempt = result.scalar_one_or_none()
        if attempt is None:
            raise DownloadHealthNotFound(f"Download health attempt {attempt_id} was not found")

        staged_result = await self.db.execute(
            select(StagedTorrent).where(StagedTorrent.id == attempt.staged_torrent_id)
        )
        staged = staged_result.scalar_one_or_none()
        if staged is None:
            raise DownloadHealthNotFound("The staged torrent for this attempt was not found")
        if staged.status != "approved":
            raise DownloadRecoveryNotEligible("The staged torrent is no longer approved")
        if staged.request_id != attempt.request_id:
            raise DownloadRecoveryNotEligible(
                "The health attempt no longer owns this staged torrent"
            )

        request: Request | None = None
        if staged.request_id is not None:
            request_result = await self.db.execute(
                select(Request)
                .where(Request.id == staged.request_id)
                .options(selectinload(Request.seasons).selectinload(Season.episodes))
            )
            request = request_result.scalar_one_or_none()
        if request is None:
            raise DownloadRecoveryNotEligible("The associated request no longer exists")
        if is_terminal_request_status(request.status):
            raise DownloadRecoveryNotEligible("The associated request is already terminal")
        if attempt.status not in {HEALTH_STATUS_RECOVERY_ELIGIBLE, HEALTH_STATUS_MISSING}:
            raise DownloadRecoveryNotEligible(
                "The attempt is no longer eligible after the current qBittorrent observation"
            )

        cooldown_until = await self._record_disposition(
            attempt,
            staged,
            request,
            disposition=disposition,
            now=observed_at,
        )
        # This is only a Siftarr ownership handoff.  No qBittorrent delete or
        # filesystem cleanup is performed, so the original remains inspectable.
        staged.status = "replaced"
        staged.replaced_at = observed_at
        staged.replacement_reason = "confirmed_download_health_recovery"
        attempt.status = HEALTH_STATUS_RECOVERED
        attempt.recovery_disposition = disposition
        attempt.recovered_at = observed_at
        attempt.superseded_at = observed_at
        attempt.cooldown_until = cooldown_until
        attempt.failure_reason = "Replacement review confirmed by operator"

        affected_coverage = self._reset_request_scope(request, staged.title)
        await _add_health_event(
            self.db,
            attempt,
            event_type=HEALTH_EVENT_RECOVERED,
            now=observed_at,
            reason=disposition,
        )
        await self.db.flush()
        review_url = f"/requests/{request.id}/details"
        if review_url and attempt.coverage_scope:
            review_url += f"?download_health_scope={quote(attempt.coverage_scope, safe='')}"
        return {
            "status": HEALTH_STATUS_RECOVERED,
            "attempt_id": attempt.id,
            "staged_torrent_id": staged.id,
            "request_id": request.id,
            "disposition": disposition,
            "cooldown_until": cooldown_until.isoformat() if cooldown_until else None,
            "affected_coverage": affected_coverage,
            "original_remains_in_qbit": True,
            "replacement_review_required": True,
            "replacement_review_url": review_url,
        }
