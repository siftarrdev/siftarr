"""Service for detecting when approved/downloading torrents have finished."""

import logging
import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.siftarr.models.request import Request, RequestStatus
from app.siftarr.models.staged_torrent import StagedTorrent
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
    """Checks downloading torrents for completion and transitions requests to COMPLETED."""

    def __init__(
        self,
        db: AsyncSession,
        qbittorrent_service: QbittorrentService,
        plex_polling_service: PlexPollingService,
    ) -> None:
        self.db = db
        self.qbittorrent = qbittorrent_service
        self.plex_polling = plex_polling_service
        self.lifecycle = LifecycleService(db)

    async def check_downloading_requests(self) -> int:
        """Check all approved torrents and complete requests when downloads finish.

        Steps:
        1. Query StagedTorrents with status=="approved" whose Request is DOWNLOADING.
        2. For each torrent determine qBit progress (via hash or name fragment).
        3. Mark torrents as qBit-done when progress >= 1.0 or not found in qBit.
        4. When ALL approved torrents for a request are qBit-done, check Plex.
        5. If Plex confirms availability, mark the request COMPLETED via lifecycle.

        Returns:
            Number of requests completed this cycle.
        """
        # 1. Fetch all approved torrents whose request is DOWNLOADING or STAGED
        stmt = (
            select(StagedTorrent, Request)
            .join(Request, Request.id == StagedTorrent.request_id)
            .where(
                StagedTorrent.status == "approved",
                Request.status.in_([RequestStatus.DOWNLOADING, RequestStatus.STAGED]),
            )
        )
        rows = list((await self.db.execute(stmt)).all())

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

        # 4. Group by request_id: check if all approved torrents for each request are done
        request_map: dict[int, tuple[Request, list[StagedTorrent]]] = {}
        for torrent, request in rows:
            if request.id not in request_map:
                request_map[request.id] = (request, [])
            request_map[request.id][1].append(torrent)

        completed = 0
        for request_id, (request, torrents) in request_map.items():
            if not all(t.id in done_torrent_ids for t in torrents):
                continue

            # 4b. All done – check Plex
            logger.info(
                "DownloadCompletionService: all torrents done for request_id=%s title=%s, checking Plex",
                request_id,
                request.title,
            )

            # Delegate to PlexPollingService for a targeted poll on this request.
            # We reuse the existing poll() which checks all non-terminal requests, but
            # the cheapest path is to call the internal _check_* helpers directly.
            try:
                from sqlalchemy.orm import selectinload

                from app.siftarr.models.request import MediaType
                from app.siftarr.models.season import Season
                from app.siftarr.services.plex_polling_service import PollDecision

                # Reload the request with season/episode relationships
                req_result = await self.db.execute(
                    select(Request)
                    .where(Request.id == request_id)
                    .options(selectinload(Request.seasons).selectinload(Season.episodes))
                )
                full_request = req_result.scalar_one_or_none()
                if full_request is None:
                    continue

                if full_request.media_type == MediaType.MOVIE:
                    decision: PollDecision | None = await self.plex_polling._check_movie(
                        full_request
                    )
                else:
                    decision = await self.plex_polling._check_tv(full_request)

                if decision is not None:
                    await self.plex_polling._apply_decision(full_request, decision)
                    completed += 1
                    logger.info(
                        "DownloadCompletionService: completed request_id=%s title=%s",
                        request_id,
                        request.title,
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
