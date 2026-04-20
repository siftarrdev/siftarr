"""Service for detecting when approved/downloading torrents have finished."""

import logging
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.siftarr.models.request import (
    ACTIVE_STAGING_WORKFLOW_STATUSES,
    Request,
    is_active_staging_workflow_status,
)
from app.siftarr.models.staged_torrent import StagedTorrent
from app.siftarr.services.activity_log_service import ActivityLogService
from app.siftarr.services.lifecycle_service import LifecycleService
from app.siftarr.services.plex_polling_service import PlexPollingService
from app.siftarr.services.qbittorrent_service import QbittorrentService

logger = logging.getLogger(__name__)

_BTIH_RE = re.compile(r"urn:btih:([0-9a-fA-F]{40}|[2-7A-Za-z]{32})", re.IGNORECASE)


def _extract_hash(magnet_url: str | None) -> str | None:
    """Extract the info-hash from a magnet URI."""
    if not magnet_url:
        return None
    m = _BTIH_RE.search(magnet_url)
    return m.group(1).lower() if m else None


class DownloadCompletionService:
    """Checks finished downloads and reconciles request availability via Plex."""

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
        done_torrent_ids: set[int] = set()
        for torrent, _request in rows:
            torrent_hash = _extract_hash(torrent.magnet_url)
            progress: float | None = None

            if torrent_hash:
                info = await self.qbittorrent.get_torrent_info(torrent_hash)
                if info is not None:
                    progress = info["progress"]
                # If info is None, torrent not found in qBit → treat as done
            else:
                progress = await self.qbittorrent.get_torrent_progress_by_name(torrent.title)

            qbit_done = (progress is None) or (progress >= 1.0)
            if qbit_done:
                done_torrent_ids.add(torrent.id)

        # 4. Group by request_id: check Plex once per request when any approved torrent is done
        request_map: dict[int, tuple[Request, list[StagedTorrent]]] = {}
        for torrent, request in rows:
            if request.id not in request_map:
                request_map[request.id] = (request, [])
            request_map[request.id][1].append(torrent)

        completed = 0
        for request_id, (request, torrents) in request_map.items():
            done_torrents = [t for t in torrents if t.id in done_torrent_ids]
            if not done_torrents:
                continue

            # 4b. At least one torrent is done/missing – check Plex immediately.
            logger.info(
                "DownloadCompletionService: %d/%d torrent(s) done for request_id=%s title=%s, checking Plex",
                len(done_torrents),
                len(torrents),
                request_id,
                request.title,
            )

            try:
                from app.siftarr.models.activity_log import EventType

                activity_log = ActivityLogService(self.db)
                await activity_log.log(
                    EventType.DOWNLOAD_COMPLETED,
                    request_id=request_id,
                    details={
                        "title": request.title,
                        "torrent_count": len(torrents),
                    },
                )
            except Exception:
                logger.exception("Failed to log download_completed for request_id=%s", request_id)

            try:
                reconcile_result = await self.plex_polling.reconcile_request(request_id)

                if reconcile_result.available:
                    completed += 1

                    try:
                        from app.siftarr.models.activity_log import EventType

                        activity_log = ActivityLogService(self.db)
                        await activity_log.log(
                            EventType.PLEX_AVAILABLE,
                            request_id=request_id,
                            details={
                                "title": request.title,
                                "reason": reconcile_result.reason,
                            },
                        )
                    except Exception:
                        logger.exception(
                            "Failed to log plex_available for request_id=%s",
                            request_id,
                        )

                    logger.info(
                        "DownloadCompletionService: reconciled request_id=%s title=%s via Plex (%s)",
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
                logger.exception(
                    "DownloadCompletionService: error checking Plex for request_id=%s", request_id
                )

        logger.info("DownloadCompletionService: completed %d request(s) this cycle", completed)
        return completed
