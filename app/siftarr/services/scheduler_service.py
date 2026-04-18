"""Background task scheduler using APScheduler."""

import logging
from contextlib import suppress

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import select

from app.siftarr.models.pending_queue import PendingQueue
from app.siftarr.models.request import MediaType, Request, RequestStatus
from app.siftarr.services.lifecycle_service import LifecycleService
from app.siftarr.services.media_helpers import extract_media_title_and_year
from app.siftarr.services.movie_decision_service import MovieDecisionService
from app.siftarr.services.overseerr_service import OverseerrService
from app.siftarr.services.pending_queue_service import PendingQueueService
from app.siftarr.services.plex_polling_service import PlexPollingService
from app.siftarr.services.plex_service import PlexService
from app.siftarr.services.prowlarr_service import ProwlarrService
from app.siftarr.services.qbittorrent_service import QbittorrentService
from app.siftarr.services.runtime_settings import get_effective_settings
from app.siftarr.services.tv_decision_service import TVDecisionService
from app.siftarr.services.unreleased_service import UnreleasedEvaluator


class SchedulerService:
    """
    Background task scheduler using APScheduler.

    Jobs:
    - Daily retry of pending queue items
    - Polling Overseerr for new approved requests
    """

    def __init__(self, db_session_factory, logger: logging.Logger | None = None) -> None:
        """
        Initialize scheduler service.

        Args:
            db_session_factory: Callable that returns an AsyncSession
            logger: Optional logger instance
        """
        self.db_session_factory = db_session_factory
        self.scheduler: AsyncIOScheduler | None = None
        self._logger = logger or logging.getLogger(__name__)

    async def _process_pending_item(self, pending_item: PendingQueue) -> None:
        """Process a single pending item."""
        logger = self._logger
        async with self.db_session_factory() as db:
            result = await db.execute(
                select(Request).where(Request.id == pending_item.request_id),
            )
            request = result.scalar_one_or_none()

            if not request:
                logger.debug(
                    "Request %s not found, removing from pending queue", pending_item.request_id
                )
                await db.delete(pending_item)
                await db.commit()
                return

            logger.info(
                "Processing pending request: request_id=%s title=%s media_type=%s",
                request.id,
                request.title,
                request.media_type.value,
            )

            runtime_settings = await get_effective_settings(db)
            prowlarr = ProwlarrService(settings=runtime_settings)
            qbittorrent = QbittorrentService(settings=runtime_settings)

            if request.year is None and (request.tmdb_id or request.tvdb_id):
                media_id = request.tmdb_id or request.tvdb_id
                if media_id is None:
                    return
                overseerr = OverseerrService(settings=runtime_settings)
                try:
                    media_type_for_api = "movie" if request.media_type == MediaType.MOVIE else "tv"
                    _title, year = await extract_media_title_and_year(
                        overseerr, media_type_for_api, media_id
                    )
                    if year is not None:
                        lifecycle = LifecycleService(db)
                        await lifecycle.update_request_metadata(request.id, year=year)
                        await db.refresh(request)
                        logger.info("Backfilled year=%s for request_id=%s", year, request.id)
                except Exception:
                    pass
                finally:
                    await overseerr.close()

            if request.media_type == MediaType.TV:
                decision_service = TVDecisionService(db, prowlarr, qbittorrent)
            else:
                decision_service = MovieDecisionService(db, prowlarr, qbittorrent)

            try:
                result = await decision_service.process_request(request.id)

                if result["status"] == "completed":
                    queue_service = PendingQueueService(db)
                    await queue_service.remove_from_queue(request.id)
                    logger.info("Request %s completed successfully", request.id)
                else:
                    queue_service = PendingQueueService(db)
                    await queue_service.update_error(
                        request.id,
                        result.get("message", "Unknown error"),
                    )
                    logger.info(
                        "Request %s still pending: %s",
                        request.id,
                        result.get("message", "Unknown error"),
                    )
            except Exception as e:
                logger.error("Error processing request %s: %s", request.id, e)
                queue_service = PendingQueueService(db)
                await queue_service.update_error(request.id, str(e))

    async def _retry_pending_jobs(self) -> None:
        """Daily job to retry pending items."""
        logger = self._logger
        async with self.db_session_factory() as db:
            queue_service = PendingQueueService(db)
            pending_items = await queue_service.get_ready_for_retry()

            if not pending_items:
                return

            logger.info("Daily pending queue retry: processing %s items", len(pending_items))

            for item in pending_items:
                try:
                    await self._process_pending_item(item)
                except Exception as e:
                    logger.error("Error processing pending item %s: %s", item.request_id, e)

    async def _poll_plex_availability(self) -> None:
        """Poll Plex to check if any active requests have become available."""
        logger = self._logger
        try:
            async with self.db_session_factory() as db:
                runtime_settings = await get_effective_settings(db)
                plex = PlexService(settings=runtime_settings)
                polling_service = PlexPollingService(db, plex)
                completed = await polling_service.poll()
                if completed:
                    logger.info("Plex polling completed %d request(s)", completed)
        except Exception:
            logger.exception("Error during Plex availability polling")

    async def _recheck_unreleased(self) -> None:
        """Re-evaluate requests currently in the UNRELEASED state."""
        logger = self._logger
        try:
            async with self.db_session_factory() as db:
                lifecycle = LifecycleService(db)
                unreleased_requests = await lifecycle.get_unreleased_requests(limit=500)
                if not unreleased_requests:
                    return

                runtime_settings = await get_effective_settings(db)
                overseerr = OverseerrService(settings=runtime_settings)
                try:
                    evaluator = UnreleasedEvaluator(db, overseerr)
                    queue_service = PendingQueueService(db)
                    for request in unreleased_requests:
                        new_status = await evaluator.evaluate_and_apply(request)
                        if new_status == RequestStatus.PENDING:
                            await queue_service.add_to_queue(request.id)
                    logger.info("Rechecked %d unreleased request(s)", len(unreleased_requests))
                finally:
                    await overseerr.close()
        except Exception:
            logger.exception("Error during unreleased recheck")

    async def _poll_overseerr(self) -> None:
        """
        Poll Overseerr for new approved requests.

        This finds requests that were approved in Overseerr but
        don't exist in our local database yet.
        """
        # TODO: Implement Overseerr polling
        # This would require:
        # 1. Query Overseerr API for approved requests
        # 2. Check which ones aren't in local DB
        # 3. Create Request entries for new ones
        # 4. Queue them for processing
        pass

    def start(self) -> None:
        """Start the background scheduler."""
        logger = self._logger
        if self.scheduler is not None:
            return

        self.scheduler = AsyncIOScheduler(
            job_defaults={
                "misfire_grace_time": 60
                * 60,  # Allow jobs to fire up to 1 hour late (e.g. after slow startup)
                "coalesce": True,  # If multiple firings were missed, only run once
            }
        )

        self.scheduler.add_job(
            self._retry_pending_jobs,
            trigger=IntervalTrigger(hours=24),
            id="retry_pending",
            name="Retry pending queue items",
            replace_existing=True,
        )

        self.scheduler.add_job(
            self._poll_overseerr,
            trigger=IntervalTrigger(hours=1),
            id="poll_overseerr",
            name="Poll Overseerr for new requests",
            replace_existing=True,
        )

        from app.siftarr.config import get_settings

        settings = get_settings()
        self.scheduler.add_job(
            self._poll_plex_availability,
            trigger=IntervalTrigger(minutes=settings.plex_poll_interval_minutes),
            id="poll_plex_availability",
            name="Poll Plex for media availability",
            replace_existing=True,
        )

        self.scheduler.add_job(
            self._recheck_unreleased,
            trigger=IntervalTrigger(hours=6),
            id="recheck_unreleased",
            name="Recheck unreleased media status",
            replace_existing=True,
        )

        self.scheduler.start()
        logger.info("Background scheduler started")

    def stop(self) -> None:
        """Stop the background scheduler."""
        logger = self._logger
        if self.scheduler is not None:
            self.scheduler.shutdown()
            self.scheduler = None
            logger.info("Background scheduler stopped")

    async def trigger_retry_now(self) -> int:
        """
        Manually trigger retry of all ready pending items.

        Returns:
            Number of items processed
        """
        async with self.db_session_factory() as db:
            queue_service = PendingQueueService(db)
            pending_items = await queue_service.get_ready_for_retry()

            for item in pending_items:
                with suppress(Exception):
                    await self._process_pending_item(item)

            return len(pending_items)
