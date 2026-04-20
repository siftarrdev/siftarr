"""Service for polling Plex to check if requested media has become available."""

import asyncio
import contextlib
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TypeVar
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.siftarr.config import get_settings
from app.siftarr.models.request import MediaType, Request, RequestStatus
from app.siftarr.models.season import Season
from app.siftarr.services.async_utils import gather_limited
from app.siftarr.services.episode_sync_service import EpisodeSyncService
from app.siftarr.services.lifecycle_service import LifecycleService
from app.siftarr.services.plex_scan_state_service import PlexScanStateService
from app.siftarr.services.plex_service import PlexService, PlexTransientScanError

logger = logging.getLogger(__name__)
ProgressCallback = Callable[[dict[str, object]], Awaitable[None] | None]
T = TypeVar("T")

# All non-terminal statuses
NON_TERMINAL_STATUSES = [
    RequestStatus.RECEIVED,
    RequestStatus.SEARCHING,
    RequestStatus.PENDING,
    RequestStatus.PARTIALLY_AVAILABLE,
    RequestStatus.UNRELEASED,
    RequestStatus.STAGED,
    RequestStatus.DOWNLOADING,
]

FULL_RECONCILE_STATUSES = [
    *NON_TERMINAL_STATUSES,
    RequestStatus.AVAILABLE,
    RequestStatus.COMPLETED,
]

NEGATIVE_RECONCILE_STATUSES = {
    RequestStatus.AVAILABLE,
    RequestStatus.PARTIALLY_AVAILABLE,
    RequestStatus.COMPLETED,
}

type EpisodeKey = tuple[int, int]


@dataclass(frozen=True)
class PollDecision:
    """Immutable polling result produced by the read-only probe stage."""

    request_id: int
    reason: str
    requested_episode_count: int = 0
    completed_episodes: frozenset[EpisodeKey] = field(default_factory=frozenset)
    episode_availability: dict[EpisodeKey, bool] = field(default_factory=dict)


@dataclass(frozen=True)
class ScanCheckpointAdvance:
    """Checkpoint advancement details recorded for scan-style runs."""

    previous_checkpoint_at: datetime | None = None
    current_checkpoint_at: datetime | None = None
    advanced: bool = False

    def as_dict(self) -> dict[str, object]:
        return {
            "previous_checkpoint_at": self.previous_checkpoint_at.isoformat()
            if self.previous_checkpoint_at
            else None,
            "current_checkpoint_at": self.current_checkpoint_at.isoformat()
            if self.current_checkpoint_at
            else None,
            "advanced": self.advanced,
        }


@dataclass
class ScanMetrics:
    """Compact scan metrics shared by incremental and full scan entry points."""

    scanned_items: int = 0
    matched_requests: int = 0
    deduped_items: int = 0
    downgraded_requests: int = 0
    skipped_on_error_items: int = 0
    checkpoint: ScanCheckpointAdvance = field(default_factory=ScanCheckpointAdvance)

    def as_dict(self) -> dict[str, object]:
        return {
            "scanned_items": self.scanned_items,
            "matched_requests": self.matched_requests,
            "deduped_items": self.deduped_items,
            "downgraded_requests": self.downgraded_requests,
            "skipped_on_error_items": self.skipped_on_error_items,
            "checkpoint": self.checkpoint.as_dict(),
        }


@dataclass(frozen=True)
class MediaIdentity:
    """Deduplication key for request probe and scan cycles."""

    media_type: MediaType
    tmdb_id: int | None = None
    tvdb_id: int | None = None
    plex_rating_key: str | None = None
    request_id: int | None = None


@dataclass(frozen=True)
class ScanProbeResult:
    """Read-side result emitted before serialized write application."""

    decisions: tuple[PollDecision, ...] = ()
    matched_requests: int = 0
    skipped_on_error_items: int = 0


@dataclass(frozen=True)
class ScanRunResult:
    """Shared result contract for scan-oriented entry points."""

    mode: str
    completed_requests: int = 0
    metrics: ScanMetrics = field(default_factory=ScanMetrics)
    clean_run: bool = True
    last_error: str | None = None


@dataclass(frozen=True)
class RecentScanMatch:
    """Recently-added Plex item and the affected Siftarr requests."""

    media_identity: MediaIdentity
    item: dict[str, object]
    requests: tuple[Request, ...] = ()


class PlexPollingService:
    """Polls Plex to check if requested media has become available."""

    def __init__(self, db: AsyncSession, plex: PlexService) -> None:
        self.db = db
        self.plex = plex
        self.lifecycle = LifecycleService(db)
        self.episode_sync = EpisodeSyncService(db, plex=plex)
        self.scan_state = PlexScanStateService(db)
        self._write_lock = asyncio.Lock()

    async def get_active_requests(self) -> list[Request]:
        """Return all non-terminal requests tracked for Plex polling."""
        result = await self.db.execute(
            select(Request)
            .where(Request.status.in_(NON_TERMINAL_STATUSES))
            .options(selectinload(Request.seasons).selectinload(Season.episodes))
        )
        return list(result.scalars().all())

    async def poll(self, on_progress: ProgressCallback | None = None) -> int:
        """Check all active requests against Plex availability.

        Returns:
            Number of requests transitioned to COMPLETED.
        """
        result = await self._run_cycle(
            mode="poll",
            progress_phase="polling",
            on_progress=on_progress,
            dedupe_within_cycle=False,
        )
        logger.info(
            "PlexPollingService: completed %d request(s) this cycle",
            result.completed_requests,
        )
        return result.completed_requests

    async def get_full_reconcile_requests(self) -> list[Request]:
        """Return all requests that can be positively or negatively reconciled."""
        result = await self.db.execute(
            select(Request)
            .where(Request.status.in_(FULL_RECONCILE_STATUSES))
            .options(selectinload(Request.seasons).selectinload(Season.episodes))
        )
        return list(result.scalars().all())

    async def incremental_recent_scan(
        self,
        on_progress: ProgressCallback | None = None,
        *,
        acquire_lock: bool = True,
        previous_checkpoint_at: datetime | None = None,
    ) -> ScanRunResult:
        """Run the fast recently-added scan with optional persisted lock ownership."""
        mode = "incremental_recent_scan"
        job_name = "plex_recent_scan"
        metrics = ScanMetrics()
        lock_owner: str | None = None

        if acquire_lock:
            lock_owner = f"{job_name}:{uuid4()}"
            await self.scan_state.recover_stale_lock(job_name)
            state = await self.scan_state.acquire_lock(
                job_name,
                lock_owner,
                self._get_incremental_lock_lease_duration(),
                now=self._current_time(),
            )
            if state is None:
                logger.info("PlexPollingService: incremental run skipped due to lock contention")
                return ScanRunResult(mode=mode, metrics=metrics)

            previous_checkpoint_at = self._coerce_datetime(getattr(state, "checkpoint_at", None))
        else:
            previous_checkpoint_at = self._coerce_datetime(previous_checkpoint_at)

        run_started_at = self._current_time()
        metrics.checkpoint = ScanCheckpointAdvance(
            previous_checkpoint_at=previous_checkpoint_at,
            current_checkpoint_at=previous_checkpoint_at,
            advanced=False,
        )

        last_error: str | None = None
        clean_run = False

        try:
            result, last_error, clean_run = await self._run_incremental_recent_scan(
                previous_checkpoint_at=previous_checkpoint_at,
                on_progress=on_progress,
            )
            metrics = result.metrics
            if clean_run:
                metrics.checkpoint = ScanCheckpointAdvance(
                    previous_checkpoint_at=previous_checkpoint_at,
                    current_checkpoint_at=run_started_at,
                    advanced=True,
                )
            else:
                metrics.checkpoint = ScanCheckpointAdvance(
                    previous_checkpoint_at=previous_checkpoint_at,
                    current_checkpoint_at=previous_checkpoint_at,
                    advanced=False,
                )

            if acquire_lock and lock_owner is not None:
                await self.scan_state.release_lock(
                    job_name,
                    lock_owner,
                    success=clean_run,
                    finished_at=self._current_time(),
                    checkpoint_at=run_started_at if clean_run else previous_checkpoint_at,
                    metrics_payload=metrics.as_dict(),
                    last_error=last_error,
                )
            if clean_run:
                logger.info(
                    "PlexPollingService: incremental run completed cleanly; "
                    "completed=%d scanned=%d matched=%d deduped=%d checkpoint_advanced=%s",
                    result.completed_requests,
                    metrics.scanned_items,
                    metrics.matched_requests,
                    metrics.deduped_items,
                    metrics.checkpoint.advanced,
                )
            else:
                logger.info(
                    "PlexPollingService: incremental run completed partially; "
                    "completed=%d scanned=%d matched=%d deduped=%d skipped_on_error=%d "
                    "checkpoint_retained=%s",
                    result.completed_requests,
                    metrics.scanned_items,
                    metrics.matched_requests,
                    metrics.deduped_items,
                    metrics.skipped_on_error_items,
                    not metrics.checkpoint.advanced,
                )
            return ScanRunResult(
                mode=mode,
                completed_requests=result.completed_requests,
                metrics=metrics,
                clean_run=clean_run,
                last_error=last_error,
            )
        except Exception as exc:
            last_error = str(exc)
            metrics.checkpoint = ScanCheckpointAdvance(
                previous_checkpoint_at=previous_checkpoint_at,
                current_checkpoint_at=previous_checkpoint_at,
                advanced=False,
            )
            if acquire_lock and lock_owner is not None:
                await self.scan_state.release_lock(
                    job_name,
                    lock_owner,
                    success=False,
                    finished_at=self._current_time(),
                    checkpoint_at=previous_checkpoint_at,
                    metrics_payload=metrics.as_dict(),
                    last_error=last_error,
                )
            raise

    async def full_reconcile_scan(
        self, on_progress: ProgressCallback | None = None
    ) -> ScanRunResult:
        """Run an authoritative full-library reconcile with guarded negative sync."""
        requests = await self.get_full_reconcile_requests()
        metrics = ScanMetrics()

        if not requests:
            logger.debug("PlexPollingService: no requests for full_reconcile_scan")
            return ScanRunResult(mode="full_reconcile_scan", metrics=metrics)

        async with self.plex.scan_cycle():
            movie_scan, tv_scan = await asyncio.gather(
                self.plex.scan_library_items("movie"),
                self.plex.scan_library_items("show"),
            )

            metrics.scanned_items = len(movie_scan.items) + len(tv_scan.items)
            metrics.skipped_on_error_items = len(movie_scan.failed_sections) + len(
                tv_scan.failed_sections
            )

            movie_presence = self._index_full_scan_items(movie_scan.items)
            tv_presence = self._index_full_scan_items(tv_scan.items)
            metrics.deduped_items = self._count_full_scan_deduped_items(
                movie_scan.items
            ) + self._count_full_scan_deduped_items(tv_scan.items)

            (
                matched_requests,
                completed_requests,
                downgraded_requests,
                skipped_requests,
            ) = await self._reconcile_requests_from_full_view(
                requests,
                movie_presence=movie_presence,
                movie_authoritative=movie_scan.authoritative,
                tv_presence=tv_presence,
                tv_authoritative=tv_scan.authoritative,
                on_progress=on_progress,
            )

        metrics.matched_requests = matched_requests
        metrics.downgraded_requests = downgraded_requests
        metrics.skipped_on_error_items += skipped_requests
        if metrics.skipped_on_error_items:
            logger.info(
                "PlexPollingService: full reconcile completed partially with guarded negative "
                "reconciliation; completed=%d matched=%d downgraded=%d skipped_on_error=%d",
                completed_requests,
                metrics.matched_requests,
                metrics.downgraded_requests,
                metrics.skipped_on_error_items,
            )
        elif metrics.downgraded_requests:
            logger.info(
                "PlexPollingService: full reconcile completed with guarded negative reconciliation; "
                "completed=%d matched=%d downgraded=%d",
                completed_requests,
                metrics.matched_requests,
                metrics.downgraded_requests,
            )
        else:
            logger.info(
                "PlexPollingService: full reconcile completed cleanly; completed=%d matched=%d",
                completed_requests,
                metrics.matched_requests,
            )
        return ScanRunResult(
            mode="full_reconcile_scan",
            completed_requests=completed_requests,
            metrics=metrics,
        )

    def _get_concurrency_limit(self) -> int:
        settings = getattr(self.plex, "settings", None)
        configured = getattr(settings, "plex_sync_concurrency", None)
        if isinstance(configured, int) and configured > 0:
            return configured
        return max(1, get_settings().plex_sync_concurrency)

    def _get_incremental_checkpoint_buffer(self) -> timedelta:
        settings = getattr(self.plex, "settings", None)
        configured = getattr(settings, "plex_checkpoint_buffer_minutes", None)
        if isinstance(configured, int) and configured >= 0:
            return timedelta(minutes=configured)
        return timedelta(minutes=max(0, get_settings().plex_checkpoint_buffer_minutes))

    def _get_incremental_lock_lease_duration(self) -> timedelta:
        settings = getattr(self.plex, "settings", None)
        configured = getattr(settings, "plex_recent_scan_interval_minutes", None)
        if isinstance(configured, int) and configured > 0:
            return timedelta(minutes=max(5, configured * 2))
        return timedelta(minutes=max(5, get_settings().plex_recent_scan_interval_minutes * 2))

    def _current_time(self) -> datetime:
        return datetime.now(UTC)

    async def _run_cycle(
        self,
        *,
        mode: str,
        progress_phase: str,
        on_progress: ProgressCallback | None,
        dedupe_within_cycle: bool,
    ) -> ScanRunResult:
        requests = await self.get_active_requests()

        if not requests:
            logger.debug("PlexPollingService: no active requests for %s", mode)
            return ScanRunResult(mode=mode)

        logger.info("PlexPollingService: %s examining %d active request(s)", mode, len(requests))
        metrics = ScanMetrics()
        request_groups = self._group_requests_by_media_identity(
            requests, dedupe_within_cycle=dedupe_within_cycle
        )
        metrics.scanned_items = len(request_groups)
        metrics.deduped_items = len(requests) - len(request_groups)

        async def emit(payload: dict[str, object]) -> None:
            if on_progress is None:
                return
            result = on_progress(payload)
            if asyncio.iscoroutine(result):
                await result

        active_titles: list[str] = []
        active_lock = asyncio.Lock()
        started = 0
        finished = 0

        async def run(group: tuple[MediaIdentity, tuple[Request, ...]]) -> ScanProbeResult:
            nonlocal started, finished

            _, grouped_requests = group
            representative = grouped_requests[0]
            title = representative.title or f"Request #{representative.id}"

            async with active_lock:
                active_titles.append(title)
                active_snapshot = active_titles[:16]
                started += 1

            await emit(
                {
                    "phase": progress_phase,
                    "current": started,
                    "total": len(request_groups),
                    "title": title,
                    "active": active_snapshot,
                }
            )

            try:
                return await self._probe_request_group(grouped_requests)
            finally:
                async with active_lock:
                    with contextlib.suppress(ValueError):
                        active_titles.remove(title)
                    finished += 1
                    active_snapshot = active_titles[:16]

                await emit(
                    {
                        "phase": progress_phase,
                        "current": finished,
                        "total": len(request_groups),
                        "title": title,
                        "active": active_snapshot,
                    }
                )

        probe_results = await gather_limited(
            list(request_groups.items()),
            self._get_concurrency_limit(),
            run,
        )

        decisions = [decision for result in probe_results for decision in result.decisions]
        metrics.matched_requests = sum(result.matched_requests for result in probe_results)
        metrics.skipped_on_error_items = sum(
            result.skipped_on_error_items for result in probe_results
        )
        completed = await self._apply_decisions(requests, decisions)
        return ScanRunResult(mode=mode, completed_requests=completed, metrics=metrics)

    async def _run_incremental_recent_scan(
        self,
        *,
        previous_checkpoint_at: datetime | None,
        on_progress: ProgressCallback | None,
    ) -> tuple[ScanRunResult, str | None, bool]:
        requests = await self.get_active_requests()
        metrics = ScanMetrics()

        if not requests:
            logger.debug("PlexPollingService: no active requests for incremental_recent_scan")
            return ScanRunResult(mode="incremental_recent_scan", metrics=metrics), None, True

        checkpoint_cutoff = self._get_incremental_cutoff(previous_checkpoint_at)
        request_identity_index = self._index_requests_by_media_identity(requests)

        async with self.plex.scan_cycle():
            recent_matches, recent_error_messages = await self._collect_recent_matches(
                request_identity_index=request_identity_index,
                checkpoint_cutoff=checkpoint_cutoff,
            )

            metrics.scanned_items = len(recent_matches)
            metrics.deduped_items = sum(
                max(0, len(match.requests) - 1) for match in recent_matches if match.requests
            )

            probe_results = await self._probe_recent_matches(
                recent_matches,
                on_progress=on_progress,
            )

        decisions = [decision for result in probe_results for decision in result.decisions]
        metrics.matched_requests = sum(result.matched_requests for result in probe_results)
        metrics.skipped_on_error_items = len(recent_error_messages) + sum(
            result.skipped_on_error_items for result in probe_results
        )

        completed = await self._apply_decisions(requests, decisions)
        last_error = self._build_incremental_error_message(
            recent_error_messages=recent_error_messages,
            skipped_on_error_items=metrics.skipped_on_error_items,
        )
        clean_run = metrics.skipped_on_error_items == 0
        return (
            ScanRunResult(
                mode="incremental_recent_scan",
                completed_requests=completed,
                metrics=metrics,
                clean_run=clean_run,
                last_error=last_error,
            ),
            last_error,
            clean_run,
        )

    def _group_requests_by_media_identity(
        self, requests: list[Request], *, dedupe_within_cycle: bool
    ) -> dict[MediaIdentity, tuple[Request, ...]]:
        grouped: dict[MediaIdentity, list[Request]] = {}
        for req in requests:
            identity = self._get_media_identity(req, dedupe_within_cycle=dedupe_within_cycle)
            grouped.setdefault(identity, []).append(req)
        return {identity: tuple(group) for identity, group in grouped.items()}

    def _index_requests_by_media_identity(
        self, requests: list[Request]
    ) -> dict[MediaIdentity, tuple[Request, ...]]:
        indexed: dict[MediaIdentity, dict[int, Request]] = {}
        for req in requests:
            for identity in self._get_request_media_identity_candidates(req):
                indexed.setdefault(identity, {})[req.id] = req
        return {
            identity: tuple(requests_by_id.values()) for identity, requests_by_id in indexed.items()
        }

    def _get_media_identity(self, req: Request, *, dedupe_within_cycle: bool) -> MediaIdentity:
        request_id = req.id
        if not dedupe_within_cycle:
            return MediaIdentity(req.media_type, request_id=request_id)

        plex_rating_key = getattr(req, "plex_rating_key", None)
        if req.media_type == MediaType.MOVIE:
            if plex_rating_key:
                return MediaIdentity(MediaType.MOVIE, plex_rating_key=plex_rating_key)
            if req.tmdb_id is not None:
                return MediaIdentity(MediaType.MOVIE, tmdb_id=req.tmdb_id)
            return MediaIdentity(MediaType.MOVIE, request_id=request_id)

        if plex_rating_key:
            return MediaIdentity(MediaType.TV, plex_rating_key=plex_rating_key)
        if req.tmdb_id is not None:
            return MediaIdentity(MediaType.TV, tmdb_id=req.tmdb_id)
        if req.tvdb_id is not None:
            return MediaIdentity(MediaType.TV, tvdb_id=req.tvdb_id)
        return MediaIdentity(MediaType.TV, request_id=request_id)

    def _get_request_media_identity_candidates(self, req: Request) -> set[MediaIdentity]:
        candidates = {self._get_media_identity(req, dedupe_within_cycle=True)}
        plex_rating_key = getattr(req, "plex_rating_key", None)
        if plex_rating_key:
            candidates.add(MediaIdentity(req.media_type, plex_rating_key=plex_rating_key))
        if req.tmdb_id is not None:
            candidates.add(MediaIdentity(req.media_type, tmdb_id=req.tmdb_id))
        if req.media_type == MediaType.TV and req.tvdb_id is not None:
            candidates.add(MediaIdentity(MediaType.TV, tvdb_id=req.tvdb_id))
        return candidates

    def _get_incremental_cutoff(self, checkpoint_at: datetime | None) -> datetime | None:
        checkpoint = self._coerce_datetime(checkpoint_at)
        if checkpoint is None:
            return None
        return checkpoint - self._get_incremental_checkpoint_buffer()

    def _coerce_datetime(self, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    def _parse_plex_datetime(self, value: object) -> datetime | None:
        if isinstance(value, datetime):
            return self._coerce_datetime(value)
        if isinstance(value, int | float):
            return datetime.fromtimestamp(value, tz=UTC)
        if isinstance(value, str):
            stripped = value.strip()
            if stripped.isdigit():
                return datetime.fromtimestamp(int(stripped), tz=UTC)
            with contextlib.suppress(ValueError):
                return self._coerce_datetime(datetime.fromisoformat(stripped))
        return None

    def _is_recent_item_in_window(
        self, item: dict[str, object], checkpoint_cutoff: datetime | None
    ) -> bool:
        if checkpoint_cutoff is None:
            return True
        added_at = self._parse_plex_datetime(item.get("added_at"))
        if added_at is None:
            return True
        return added_at > checkpoint_cutoff

    def _extract_guid_ids(self, item: dict[str, object]) -> tuple[int | None, int | None]:
        tmdb_id: int | None = None
        tvdb_id: int | None = None

        guid_values = item.get("guids")
        for guid in guid_values if isinstance(guid_values, tuple | list) else ():
            guid_value = str(guid)
            prefix, _, raw_id = guid_value.partition("://")
            if not raw_id.isdigit():
                continue
            if prefix in {"tmdb", "com.plexapp.agents.themoviedb"} and tmdb_id is None:
                tmdb_id = int(raw_id)
            if prefix in {"tvdb", "com.plexapp.agents.thetvdb"} and tvdb_id is None:
                tvdb_id = int(raw_id)
        return tmdb_id, tvdb_id

    def _get_recent_item_canonical_identity(self, item: dict[str, object]) -> MediaIdentity | None:
        media_type = self._get_request_media_type_for_item(item)
        if media_type is None:
            return None

        tmdb_id, tvdb_id = self._extract_guid_ids(item)
        rating_key = item.get("rating_key")

        if tmdb_id is not None:
            return MediaIdentity(media_type, tmdb_id=tmdb_id)
        if media_type == MediaType.TV and tvdb_id is not None:
            return MediaIdentity(MediaType.TV, tvdb_id=tvdb_id)
        if rating_key:
            return MediaIdentity(media_type, plex_rating_key=str(rating_key))
        return None

    def _get_recent_item_identity_candidates(self, item: dict[str, object]) -> set[MediaIdentity]:
        media_type = self._get_request_media_type_for_item(item)
        if media_type is None:
            return set()

        candidates: set[MediaIdentity] = set()
        canonical = self._get_recent_item_canonical_identity(item)
        if canonical is not None:
            candidates.add(canonical)

        rating_key = item.get("rating_key")
        if rating_key:
            candidates.add(MediaIdentity(media_type, plex_rating_key=str(rating_key)))

        tmdb_id, tvdb_id = self._extract_guid_ids(item)
        if tmdb_id is not None:
            candidates.add(MediaIdentity(media_type, tmdb_id=tmdb_id))
        if media_type == MediaType.TV and tvdb_id is not None:
            candidates.add(MediaIdentity(MediaType.TV, tvdb_id=tvdb_id))
        return candidates

    def _get_request_media_type_for_item(self, item: dict[str, object]) -> MediaType | None:
        item_type = item.get("type")
        if item_type == "movie":
            return MediaType.MOVIE
        if item_type == "show":
            return MediaType.TV
        return None

    def _index_full_scan_items(
        self, items: tuple[dict[str, object], ...]
    ) -> dict[MediaIdentity, dict[str, object]]:
        indexed: dict[MediaIdentity, dict[str, object]] = {}
        for item in items:
            normalized_item = dict(item)
            if not self._item_has_media(normalized_item):
                continue
            for identity in self._get_recent_item_identity_candidates(normalized_item):
                indexed.setdefault(identity, normalized_item)
        return indexed

    def _count_full_scan_deduped_items(self, items: tuple[dict[str, object], ...]) -> int:
        canonical_identities: set[MediaIdentity] = set()
        counted_items = 0
        for item in items:
            normalized_item = dict(item)
            if not self._item_has_media(normalized_item):
                continue
            canonical_identity = self._get_recent_item_canonical_identity(normalized_item)
            if canonical_identity is None:
                media_type = self._get_request_media_type_for_item(normalized_item)
                if media_type is None:
                    continue
                rating_key = normalized_item.get("rating_key")
                if not rating_key:
                    continue
                canonical_identity = MediaIdentity(
                    media_type=media_type, plex_rating_key=str(rating_key)
                )
            counted_items += 1
            canonical_identities.add(canonical_identity)
        return max(0, counted_items - len(canonical_identities))

    async def _reconcile_requests_from_full_view(
        self,
        requests: list[Request],
        *,
        movie_presence: dict[MediaIdentity, dict[str, object]],
        movie_authoritative: bool,
        tv_presence: dict[MediaIdentity, dict[str, object]],
        tv_authoritative: bool,
        on_progress: ProgressCallback | None,
    ) -> tuple[int, int, int, int]:
        matched_requests = 0
        completed_requests = 0
        downgraded_requests = 0
        skipped_requests = 0

        async def emit(payload: dict[str, object]) -> None:
            if on_progress is None:
                return
            result = on_progress(payload)
            if asyncio.iscoroutine(result):
                await result

        for index, req in enumerate(requests, start=1):
            await emit(
                {
                    "phase": "full_reconcile_scan",
                    "current": index,
                    "total": len(requests),
                    "title": req.title or f"Request #{req.id}",
                    "active": [],
                }
            )

            if req.media_type == MediaType.MOVIE:
                matched, completed, downgraded, skipped = await self._reconcile_movie_request(
                    req,
                    movie_presence=movie_presence,
                    authoritative=movie_authoritative,
                )
            elif req.media_type == MediaType.TV:
                matched, completed, downgraded, skipped = await self._reconcile_tv_request(
                    req,
                    tv_presence=tv_presence,
                    authoritative=tv_authoritative,
                )
            else:
                matched = completed = downgraded = skipped = 0

            matched_requests += matched
            completed_requests += completed
            downgraded_requests += downgraded
            skipped_requests += skipped

        return matched_requests, completed_requests, downgraded_requests, skipped_requests

    async def _reconcile_movie_request(
        self,
        req: Request,
        *,
        movie_presence: dict[MediaIdentity, dict[str, object]],
        authoritative: bool,
    ) -> tuple[int, int, int, int]:
        matched_item = self._find_matching_presence_item(req, movie_presence)
        if matched_item is not None:
            if req.status not in {RequestStatus.AVAILABLE, RequestStatus.COMPLETED}:
                await self._run_serialized_write(
                    self.lifecycle.transition(
                        req.id,
                        RequestStatus.COMPLETED,
                        reason="Found on Plex",
                    )
                )
                req.status = RequestStatus.COMPLETED
                return 1, 1, 0, 0
            return 1, 0, 0, 0

        if not authoritative or req.status not in NEGATIVE_RECONCILE_STATUSES:
            return 0, 0, 0, 0

        await self._run_serialized_write(
            self.lifecycle.transition(
                req.id,
                RequestStatus.PENDING,
                reason="Full Plex reconcile no longer finds this movie",
            )
        )
        req.status = RequestStatus.PENDING
        return 0, 0, 1, 0

    async def _reconcile_tv_request(
        self,
        req: Request,
        *,
        tv_presence: dict[MediaIdentity, dict[str, object]],
        authoritative: bool,
    ) -> tuple[int, int, int, int]:
        matched_show = self._find_matching_presence_item(req, tv_presence)
        if matched_show is None:
            if not authoritative or req.status not in NEGATIVE_RECONCILE_STATUSES:
                return 0, 0, 0, 0
            if not req.seasons:
                return 0, 0, 0, 1
            before_status = req.status
            await self._run_serialized_write(
                self.episode_sync.reconcile_existing_seasons_from_plex(req, req.seasons, {})
            )
            return 0, 0, int(req.status != before_status), 0

        episode_result = await self.plex.get_episode_availability_result(
            str(matched_show["rating_key"])
        )
        if not episode_result.authoritative:
            return 1, 0, 0, 1

        matched_count = 1
        before_status = req.status
        if req.seasons:
            await self._run_serialized_write(
                self.episode_sync.reconcile_existing_seasons_from_plex(
                    req,
                    req.seasons,
                    episode_result.availability,
                )
            )

        requested_episodes = self._get_requested_episodes(req)
        if requested_episodes and all(
            episode_result.availability.get(key, False) for key in requested_episodes
        ):
            return matched_count, int(req.status != before_status), 0, 0

        return matched_count, 0, int(req.status != before_status), 0

    def _find_matching_presence_item(
        self,
        req: Request,
        presence_index: dict[MediaIdentity, dict[str, object]],
    ) -> dict[str, object] | None:
        for identity in self._get_request_media_identity_candidates(req):
            item = presence_index.get(identity)
            if item is not None:
                return item
        return None

    async def _collect_recent_matches(
        self,
        *,
        request_identity_index: dict[MediaIdentity, tuple[Request, ...]],
        checkpoint_cutoff: datetime | None,
    ) -> tuple[list[RecentScanMatch], list[str]]:
        collected: dict[MediaIdentity, RecentScanMatch] = {}
        errors: list[str] = []

        for plex_media_type in ("movie", "show"):
            try:
                async for item in self.plex.iter_recently_added_items(plex_media_type):
                    normalized_item = dict(item)
                    if not self._is_recent_item_in_window(normalized_item, checkpoint_cutoff):
                        continue

                    media_identity = self._get_recent_item_canonical_identity(normalized_item)
                    if media_identity is None:
                        continue

                    matched_requests: dict[int, Request] = {}
                    for identity in self._get_recent_item_identity_candidates(normalized_item):
                        for req in request_identity_index.get(identity, ()):
                            matched_requests[req.id] = req

                    existing = collected.get(media_identity)
                    if existing is not None:
                        merged_requests = {req.id: req for req in existing.requests}
                        merged_requests.update(matched_requests)
                        existing_item = dict(existing.item)
                        if self._item_has_media(normalized_item) and not self._item_has_media(
                            existing_item
                        ):
                            existing_item = normalized_item
                        collected[media_identity] = RecentScanMatch(
                            media_identity=media_identity,
                            item=existing_item,
                            requests=tuple(merged_requests.values()),
                        )
                        continue

                    collected[media_identity] = RecentScanMatch(
                        media_identity=media_identity,
                        item=normalized_item,
                        requests=tuple(matched_requests.values()),
                    )
            except PlexTransientScanError as exc:
                logger.warning(
                    "PlexPollingService: recently-added scan for %s failed transiently: %s",
                    plex_media_type,
                    exc,
                )
                errors.append(str(exc))

        return list(collected.values()), errors

    async def _probe_recent_matches(
        self,
        recent_matches: list[RecentScanMatch],
        *,
        on_progress: ProgressCallback | None,
    ) -> list[ScanProbeResult]:
        matched = [match for match in recent_matches if match.requests]
        if not matched:
            return []

        async def emit(payload: dict[str, object]) -> None:
            if on_progress is None:
                return
            result = on_progress(payload)
            if asyncio.iscoroutine(result):
                await result

        active_titles: list[str] = []
        active_lock = asyncio.Lock()
        started = 0
        finished = 0

        async def run(match: RecentScanMatch) -> ScanProbeResult:
            nonlocal started, finished

            title = match.requests[0].title or str(match.item.get("title") or "Unknown")

            async with active_lock:
                active_titles.append(title)
                active_snapshot = active_titles[:16]
                started += 1

            await emit(
                {
                    "phase": "incremental_recent_scan",
                    "current": started,
                    "total": len(matched),
                    "title": title,
                    "active": active_snapshot,
                }
            )

            try:
                return await self._probe_recent_match(match)
            finally:
                async with active_lock:
                    with contextlib.suppress(ValueError):
                        active_titles.remove(title)
                    finished += 1
                    active_snapshot = active_titles[:16]

                await emit(
                    {
                        "phase": "incremental_recent_scan",
                        "current": finished,
                        "total": len(matched),
                        "title": title,
                        "active": active_snapshot,
                    }
                )

        return await gather_limited(matched, self._get_concurrency_limit(), run)

    async def _probe_recent_match(self, match: RecentScanMatch) -> ScanProbeResult:
        representative = match.requests[0]
        try:
            if match.media_identity.media_type == MediaType.MOVIE:
                return await self._probe_recent_movie_match(match)
            if match.media_identity.media_type == MediaType.TV:
                return await self._probe_recent_tv_match(match)
            return ScanProbeResult()
        except Exception:
            logger.exception(
                "PlexPollingService: error checking request_id=%s title=%s during incremental recent scan",
                representative.id,
                representative.title,
            )
            return ScanProbeResult(skipped_on_error_items=1)

    async def _probe_recent_movie_match(self, match: RecentScanMatch) -> ScanProbeResult:
        if self._item_has_media(match.item):
            return ScanProbeResult(
                decisions=tuple(
                    PollDecision(request_id=req.id, reason="Found on Plex")
                    for req in match.requests
                ),
                matched_requests=len(match.requests),
            )
        return await self._probe_movie_group(match.requests, authoritative_required=True)

    async def _probe_recent_tv_match(self, match: RecentScanMatch) -> ScanProbeResult:
        show = self._get_show_dict_from_recent_item(match.item)
        return await self._probe_tv_group(
            match.requests,
            show=show,
            authoritative_required=True,
        )

    def _get_show_dict_from_recent_item(self, item: dict[str, object]) -> dict[str, object] | None:
        rating_key = item.get("rating_key")
        if not rating_key:
            return None
        return {
            "rating_key": str(rating_key),
            "title": item.get("title"),
            "guid": item.get("guid"),
            "Media": item.get("Media"),
        }

    def _item_has_media(self, item: dict[str, object]) -> bool:
        return bool(item.get("Media"))

    def _build_incremental_error_message(
        self,
        *,
        recent_error_messages: list[str],
        skipped_on_error_items: int,
    ) -> str | None:
        if recent_error_messages:
            return "; ".join(recent_error_messages)
        if skipped_on_error_items:
            return (
                "Incremental recent Plex scan had transient request probe errors; "
                "checkpoint retained"
            )
        return None

    async def _probe_request_group(self, requests: tuple[Request, ...]) -> ScanProbeResult:
        representative = requests[0]
        try:
            if representative.media_type == MediaType.MOVIE:
                return await self._probe_movie_group(requests)
            if representative.media_type == MediaType.TV:
                return await self._probe_tv_group(requests)
            return ScanProbeResult()
        except Exception:
            logger.exception(
                "PlexPollingService: error checking request_id=%s title=%s",
                representative.id,
                representative.title,
            )
            return ScanProbeResult(skipped_on_error_items=1)

    async def _probe_movie_group(
        self,
        requests: tuple[Request, ...],
        *,
        authoritative_required: bool = False,
    ) -> ScanProbeResult:
        if authoritative_required:
            decision, authoritative = await self._check_movie_authoritatively(requests[0])
            if not authoritative:
                return ScanProbeResult(skipped_on_error_items=1)
        else:
            decision = await self._check_movie(requests[0])
        if decision is None:
            return ScanProbeResult()

        return ScanProbeResult(
            decisions=tuple(
                PollDecision(request_id=req.id, reason=decision.reason) for req in requests
            ),
            matched_requests=len(requests),
        )

    async def _probe_tv_group(
        self,
        requests: tuple[Request, ...],
        show: dict[str, object] | None = None,
        *,
        authoritative_required: bool = False,
    ) -> ScanProbeResult:
        if show is None:
            if authoritative_required:
                show, authoritative = await self._find_show_authoritatively(requests[0])
                if not authoritative:
                    return ScanProbeResult(skipped_on_error_items=1)
            else:
                show = await self._find_show(requests[0])
        if not show:
            return ScanProbeResult()

        rating_key = str(show["rating_key"])
        if authoritative_required:
            episode_result = await self.plex.get_episode_availability_result(rating_key)
            if not episode_result.authoritative:
                return ScanProbeResult(skipped_on_error_items=1)
            availability = episode_result.availability
        else:
            availability = await self.plex.get_episode_availability(rating_key)
            if not availability:
                return ScanProbeResult()

        decisions: list[PollDecision] = []
        for req in requests:
            requested_episodes = self._get_requested_episodes(req)
            if not requested_episodes:
                continue

            completed_episodes = frozenset(
                (season_number, episode_number)
                for season_number, episode_number in requested_episodes
                if availability.get((season_number, episode_number), False)
            )
            if len(completed_episodes) == len(requested_episodes):
                decisions.append(
                    PollDecision(
                        request_id=req.id,
                        reason="All episodes found on Plex",
                        requested_episode_count=len(requested_episodes),
                        completed_episodes=completed_episodes,
                        episode_availability=dict(availability),
                    )
                )

        return ScanProbeResult(decisions=tuple(decisions), matched_requests=len(decisions))

    async def _apply_decisions(self, requests: list[Request], decisions: list[PollDecision]) -> int:
        requests_by_id = {req.id: req for req in requests}
        completed = 0
        for decision in decisions:
            req = requests_by_id.get(decision.request_id)
            if req is None:
                continue

            await self._run_serialized_write(self._apply_decision(req, decision))
            completed += 1
        return completed

    async def _run_serialized_write(self, operation: Awaitable[T]) -> T:
        async with self._write_lock:
            return await operation

    async def _apply_decision(self, req: Request, decision: PollDecision) -> None:
        if decision.episode_availability:
            logger.info(
                "PlexPollingService: TV '%s' has %d/%d requested episode(s) available on Plex, "
                "reconciling request_id=%s",
                req.title,
                len(decision.completed_episodes),
                decision.requested_episode_count,
                req.id,
            )
            await self.episode_sync.reconcile_existing_seasons_from_plex(
                req,
                req.seasons,
                decision.episode_availability,
            )
            return

        logger.info(
            "PlexPollingService: movie '%s' (tmdb_id=%s) found on Plex, completing request_id=%s",
            req.title,
            req.tmdb_id,
            req.id,
        )

        await self.lifecycle.transition(req.id, RequestStatus.COMPLETED, reason=decision.reason)

    async def _check_movie(self, req: Request) -> PollDecision | None:
        """Check if a movie request is available on Plex."""
        if not req.tmdb_id:
            return None

        available = await self.plex.check_movie_available(req.tmdb_id)
        if available:
            return PollDecision(request_id=req.id, reason="Found on Plex")
        return None

    async def _check_movie_authoritatively(self, req: Request) -> tuple[PollDecision | None, bool]:
        """Check movie availability without collapsing transient Plex failures."""
        if not req.tmdb_id:
            return None, True

        result = await self.plex.lookup_movie_by_tmdb(req.tmdb_id)
        if result.item is None:
            return None, result.authoritative
        if self._item_has_media(result.item):
            return PollDecision(request_id=req.id, reason="Found on Plex"), True
        return None, True

    async def _check_tv(self, req: Request) -> PollDecision | None:
        """Check if a TV request is fully available on Plex."""
        result = await self._probe_tv_group((req,))
        return result.decisions[0] if result.decisions else None

    async def _find_show(self, req: Request) -> dict | None:
        """Find a show in Plex by TMDB or TVDB ID."""
        if req.tmdb_id:
            show = await self.plex.get_show_by_tmdb(req.tmdb_id)
            if show:
                return show
        if req.tvdb_id:
            show = await self.plex.get_show_by_tvdb(req.tvdb_id)
            if show:
                return show
        return None

    async def _find_show_authoritatively(
        self, req: Request
    ) -> tuple[dict[str, object] | None, bool]:
        """Find a show while preserving inconclusive lookup semantics."""
        authoritative = True

        if req.tmdb_id:
            result = await self.plex.lookup_show_by_tmdb(req.tmdb_id)
            if result.item is not None:
                return self._item_to_lookup_dict(result.item), True
            authoritative = authoritative and result.authoritative

        if req.tvdb_id:
            result = await self.plex.lookup_show_by_tvdb(req.tvdb_id)
            if result.item is not None:
                return self._item_to_lookup_dict(result.item), True
            authoritative = authoritative and result.authoritative

        return None, authoritative

    @staticmethod
    def _item_to_lookup_dict(item: dict[str, object]) -> dict[str, object]:
        """Normalize a Plex lookup item into the shape expected by probe helpers."""
        rating_key = item.get("rating_key") or item.get("ratingKey")
        return {
            "rating_key": str(rating_key) if rating_key is not None else None,
            "title": item.get("title"),
            "guid": item.get("guid"),
            "Media": item.get("Media"),
        }

    def _get_requested_episodes(self, req: Request) -> list[tuple[int, int]]:
        """Get list of (season, episode) tuples from request's seasons/episodes."""
        episodes: list[tuple[int, int]] = []

        # Use the ORM relationships if loaded
        if req.seasons:
            for season in req.seasons:
                for ep in season.episodes:
                    episodes.append((season.season_number, ep.episode_number))
            return episodes

        # Fallback: parse requested_seasons + requested_episodes JSON strings
        if req.requested_episodes:
            try:
                ep_list = json.loads(req.requested_episodes)
                # Format: list of {"season": N, "episode": N}
                for item in ep_list:
                    if isinstance(item, dict) and "season" in item and "episode" in item:
                        episodes.append((item["season"], item["episode"]))
            except (json.JSONDecodeError, TypeError):
                pass

        return episodes

    async def _update_episode_statuses(
        self, req: Request, completed_episodes: frozenset[EpisodeKey]
    ) -> None:
        """Update episode statuses based on Plex availability."""
        for season in req.seasons:
            for ep in season.episodes:
                key = (season.season_number, ep.episode_number)
                if key in completed_episodes and ep.status != RequestStatus.COMPLETED:
                    ep.status = RequestStatus.COMPLETED
            # If all episodes in season are completed, mark season completed too
            if season.episodes and all(
                e.status == RequestStatus.COMPLETED for e in season.episodes
            ):
                season.status = RequestStatus.COMPLETED
        await self.db.commit()
