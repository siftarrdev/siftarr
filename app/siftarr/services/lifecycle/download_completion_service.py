"""Service for detecting when approved/downloading torrents have finished."""

import json
import logging
import re
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.siftarr.models.activity_log import ActivityLog, EventType
from app.siftarr.models.request import (
    ACTIVE_STAGING_WORKFLOW_STATUSES,
    Request,
    RequestStatus,
    is_active_staging_workflow_status,
)
from app.siftarr.models.season import Season
from app.siftarr.models.staged_torrent import StagedTorrent
from app.siftarr.services.admin.plex_polling_service import PlexPollingService
from app.siftarr.services.integrations.qbittorrent_service import (
    QbittorrentService,
    _torrent_file_info_hash,
)
from app.siftarr.services.lifecycle.activity_log_service import ActivityLogService
from app.siftarr.services.lifecycle.lifecycle_service import LifecycleService
from app.siftarr.services.lifecycle.overseerr_sync_service import (
    approve_overseerr_request_best_effort,
)
from app.siftarr.services.releases.release_serializers import (
    scope_to_episode_set,
    serialize_target_scope,
)

logger = logging.getLogger(__name__)

_BTIH_RE = re.compile(r"urn:btih:([0-9a-fA-F]{40}|[2-7A-Za-z]{32})", re.IGNORECASE)
_NON_ALNUM_RE = re.compile(r"[^a-zA-Z0-9]+")


def _extract_hash(magnet_url: str | None, torrent_path: str | None = None) -> str | None:
    """Extract the info-hash from a magnet URI, or compute it from a .torrent file."""
    if magnet_url:
        m = _BTIH_RE.search(magnet_url)
        if m:
            return m.group(1).lower()
    if torrent_path:
        return _torrent_file_info_hash(torrent_path)
    return None


def _download_completed_torrent_ids(details: str | None) -> set[int]:
    if not details:
        return set()
    try:
        payload = json.loads(details)
    except TypeError, ValueError:
        return set()
    if not isinstance(payload, dict):
        return set()

    torrent_ids: set[int] = set()
    torrent_id = payload.get("torrent_id")
    if isinstance(torrent_id, int):
        torrent_ids.add(torrent_id)
    for item in payload.get("done_torrents") or []:
        if isinstance(item, dict) and isinstance(item.get("torrent_id"), int):
            torrent_ids.add(item["torrent_id"])
    return torrent_ids


def _normalize_name(name: str) -> str:
    """Normalize a torrent name for loose matching.

    Replaces runs of non-alphanumeric characters with a single space and
    lowercases, so that e.g. ``"Finding.Carter.S01-S02.1080p.WEB-DL"``
    and ``"Finding Carter S01-S02 1080p WEB-DL"`` compare as equal.
    """
    return _NON_ALNUM_RE.sub(" ", name).strip().lower()


class DownloadCompletionService:
    """Checks finished downloads and reconciles request availability via Plex."""

    _plex_error_backoff_until_by_request_id: dict[int, datetime] = {}
    _PLEX_ERROR_BACKOFF_SECONDS = 60

    def __init__(
        self,
        db: AsyncSession,
        qbittorrent_service: QbittorrentService,
        plex_polling_service: PlexPollingService | Any,
    ) -> None:
        self.db = db
        self.qbittorrent = qbittorrent_service
        self.plex_polling = plex_polling_service
        self.lifecycle = LifecycleService(db)

    async def check_downloading_requests(self) -> int:
        """Check all approved torrents and reconcile requests when downloads finish.

        Steps:
        1. Query StagedTorrents with status=="approved" whose Request is DOWNLOADING.
        2. For each torrent determine qBit progress (via hash or name fragment).
        3. Mark torrents as qBit-done when progress >= 1.0 or not found in qBit.
        4. When ANY approved torrent for a request is qBit-done, check Plex.
        5. If Plex confirms availability, reuse Plex polling reconciliation for the request.

        Returns:
            Number of requests reconciled this cycle.
        """
        # 1. Fetch all approved torrents whose request is still actively staged or downloading.
        stmt = (
            select(StagedTorrent, Request)
            .join(Request, Request.id == StagedTorrent.request_id)
            .where(
                StagedTorrent.status == "approved",
                Request.status.in_(ACTIVE_STAGING_WORKFLOW_STATUSES),
            )
            .options(selectinload(Request.seasons).selectinload(Season.episodes))
        )
        rows = [
            (torrent, request)
            for torrent, request in list((await self.db.execute(stmt)).all())
            if is_active_staging_workflow_status(request.status)
        ]

        if not rows:
            logger.debug("DownloadCompletionService: no active downloading torrents")
            return 0

        logger.info("DownloadCompletionService: checking %d approved torrent(s)", len(rows))

        # 2 & 3. Determine per-torrent qBit progress and which are "done"
        # Batch-fetch all qBittorrent torrents once for local matching
        all_torrents = await self.qbittorrent.get_all_active_torrents()
        by_hash: dict[str, dict[str, Any]] = {}
        by_name: dict[str, dict[str, Any]] = {}
        for t in all_torrents:
            h = t.get("hash")
            if h:
                by_hash[h.lower()] = t
            n = t.get("name")
            if n:
                by_name[n.lower()] = t

        done_torrent_ids: set[int] = set()
        qbit_evidence_by_torrent_id: dict[int, dict[str, Any]] = {}
        for torrent, _request in rows:
            # Prefer the stored info_hash (most reliable), then fall back to
            # extracting from magnet URI or computing from the .torrent file.
            # Use getattr + isinstance so MagicMock in tests doesn't auto-create
            # a fake hash that would break JSON serialisation.
            torrent_hash = _extract_hash(torrent.magnet_url, torrent.torrent_path)
            stored_hash = getattr(torrent, "info_hash", None)
            if isinstance(stored_hash, str) and stored_hash:
                torrent_hash = stored_hash
            info: dict[str, Any] | None = None
            progress: float | None = None

            if torrent_hash:
                info = by_hash.get(torrent_hash.lower())
                if info is not None:
                    progress = info.get("progress")
            else:
                # No hash available — fall back to name matching with
                # normalised separators (spaces vs dots vs dashes).
                title_norm = _normalize_name(torrent.title)
                matched = next(
                    (t for qname, t in by_name.items() if title_norm in _normalize_name(qname)),
                    None,
                )
                if matched is not None:
                    info = matched
                    progress = info.get("progress")

            # A torrent is "done" in qBittorrent only when we can confirm it:
            #   a) progress >= 1.0, or
            #   b) we identified it by hash and it's gone from qBittorrent.
            # We do NOT treat "not found by name matching" as done — the name
            # heuristic is unreliable and would falsely trigger the Plex check
            # while the torrent is still downloading.
            if progress is not None:
                qbit_done = progress >= 1.0
            elif torrent_hash and info is None:
                # Identified by hash but no longer in qBittorrent → removed after completion
                qbit_done = True
            else:
                qbit_done = False
            qbit_evidence_by_torrent_id[torrent.id] = {
                "torrent_id": torrent.id,
                "title": torrent.title,
                "hash": torrent_hash,
                "qbit_found": info is not None,
                "qbit_progress": progress,
                "qbit_state": info.get("state") if info else None,
            }
            if qbit_done:
                done_torrent_ids.add(torrent.id)

        # 4. Group by request_id: check Plex once per request when any approved torrent is done
        request_map: dict[int, tuple[Request, list[StagedTorrent]]] = {}
        for torrent, request in rows:
            if request.id not in request_map:
                request_map[request.id] = (request, [])
            request_map[request.id][1].append(torrent)

        for request, torrents in request_map.values():
            if any(qbit_evidence_by_torrent_id[t.id]["qbit_found"] for t in torrents):
                await approve_overseerr_request_best_effort(
                    self.db,
                    request,
                    reason="qbit_present_evidence",
                )

        completed = 0
        activity_log = ActivityLogService(self.db)
        ready_request_map: dict[int, tuple[Request, list[StagedTorrent], list[StagedTorrent]]] = {}
        for request_id, (request, torrents) in request_map.items():
            done_torrents = [t for t in torrents if t.id in done_torrent_ids]
            if done_torrents:
                ready_request_map[request_id] = (request, torrents, done_torrents)

        if not ready_request_map:
            logger.info("DownloadCompletionService: completed %d request(s) this cycle", completed)
            return completed

        existing_download_completed_logs_result = await self.db.execute(
            select(ActivityLog.request_id, ActivityLog.details).where(
                ActivityLog.request_id.in_(set(ready_request_map)),
                ActivityLog.event_type == EventType.DOWNLOAD_COMPLETED.value,
            )
        )
        completed_torrent_ids_by_request: dict[int, set[int]] = {
            request_id: set() for request_id in ready_request_map
        }
        for request_id, details in existing_download_completed_logs_result.all():
            if request_id is None:
                continue
            completed_torrent_ids_by_request.setdefault(request_id, set()).update(
                _download_completed_torrent_ids(details)
            )

        for request_id, (request, torrents, done_torrents) in ready_request_map.items():
            # 4b. At least one torrent is done/missing – check Plex immediately.
            logger.info(
                "DownloadCompletionService: %d/%d torrent(s) done for request_id=%s title=%s, checking Plex",
                len(done_torrents),
                len(torrents),
                request_id,
                request.title,
            )

            await approve_overseerr_request_best_effort(
                self.db,
                request,
                reason="qbit_completion_evidence",
            )

            existing_torrent_ids = completed_torrent_ids_by_request.setdefault(request_id, set())
            newly_done_torrents = [t for t in done_torrents if t.id not in existing_torrent_ids]
            if newly_done_torrents:
                await activity_log.log(
                    EventType.DOWNLOAD_COMPLETED,
                    request_id=request_id,
                    details={
                        "title": request.title,
                        "torrent_count": len(torrents),
                        "done_torrents": [
                            qbit_evidence_by_torrent_id[t.id] for t in newly_done_torrents
                        ],
                    },
                )
                existing_torrent_ids.update(t.id for t in newly_done_torrents)
                await self.db.commit()

            backoff_until = self._plex_error_backoff_until_by_request_id.get(request_id)
            if backoff_until and backoff_until > datetime.now(UTC):
                logger.info(
                    "DownloadCompletionService: request_id=%s Plex check in transient-error backoff until %s",
                    request_id,
                    backoff_until.isoformat(),
                )
                continue

            try:
                reconcile_result = await self._check_completed_download_waiting_for_plex(
                    request,
                    done_torrents,
                )
                self._plex_error_backoff_until_by_request_id.pop(request_id, None)

                if (
                    reconcile_result.available
                    and reconcile_result.status_after == RequestStatus.COMPLETED
                ):
                    completed += 1

                    await activity_log.log(
                        EventType.PLEX_AVAILABLE,
                        request_id=request_id,
                        details={
                            "title": request.title,
                            "reason": reconcile_result.reason,
                        },
                    )
                    await self.db.commit()

                    logger.log(
                        logging.INFO,
                        "DownloadCompletionService: checked request_id=%s title=%s via Plex (%s)",
                        request_id,
                        request.title,
                        reconcile_result.reason,
                    )
                else:
                    logger.info(
                        "DownloadCompletionService: request_id=%s not yet on Plex, will retry",
                        request_id,
                    )
            except Exception:
                self._plex_error_backoff_until_by_request_id[request_id] = datetime.now(
                    UTC
                ) + timedelta(seconds=self._PLEX_ERROR_BACKOFF_SECONDS)
                logger.exception(
                    "DownloadCompletionService: error checking Plex for request_id=%s", request_id
                )

        logger.info("DownloadCompletionService: completed %d request(s) this cycle", completed)
        return completed

    async def _check_completed_download_waiting_for_plex(
        self,
        request: Request,
        done_torrents: list[StagedTorrent],
    ):
        if hasattr(type(self.plex_polling), "check_completed_download_waiting_for_plex"):
            return await self.plex_polling.check_completed_download_waiting_for_plex(
                request,
                episode_keys=self._covered_episode_keys(request, done_torrents),
            )
        return await self.plex_polling.check_request(request.id)

    def _covered_episode_keys(
        self,
        request: Request,
        done_torrents: list[StagedTorrent],
    ) -> set[tuple[int, int]] | None:
        if request.media_type.value != "tv":
            return None
        requested_episode_keys = {
            (season.season_number, episode.episode_number)
            for season in request.seasons
            for episode in season.episodes
        }
        if not requested_episode_keys:
            return None
        known_season_numbers = [season.season_number for season in request.seasons]
        covered: set[tuple[int, int]] = set()
        for torrent in done_torrents:
            scope = serialize_target_scope(media_type=request.media_type, title=torrent.title)
            scope_keys = scope_to_episode_set(scope, known_season_numbers)
            if not scope_keys:
                return requested_episode_keys
            for season_number, episode_number in scope_keys:
                if episode_number is None:
                    covered.update(key for key in requested_episode_keys if key[0] == season_number)
                elif (season_number, episode_number) in requested_episode_keys:
                    covered.add((season_number, episode_number))
        return covered or None
