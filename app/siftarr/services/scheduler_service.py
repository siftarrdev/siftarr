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

            overseerr_eval = OverseerrService(settings=runtime_settings)
            try:
                evaluator = UnreleasedEvaluator(db, overseerr_eval)
                try:
                    new_status = await evaluator.evaluate_and_apply(request)
                except Exception:
                    logger.exception("Unreleased evaluation failed for request_id=%s", request.id)
                    new_status = None
                if new_status == RequestStatus.UNRELEASED:
                    await PendingQueueService(db).remove_from_queue(request.id)
                    logger.info("Request %s now unreleased; removed from pending queue", request.id)
                    return
            finally:
                await overseerr_eval.close()

            prowlarr = ProwlarrService(settings=runtime_settings)
            qbittorrent = QbittorrentService(settings=runtime_settings)

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

    async def _reevaluate_unreleased(self) -> int:
        """
        Re-evaluate all requests currently in UNRELEASED status.

        For each request, runs the UnreleasedEvaluator. If a request transitions
        to PENDING, it is enqueued in the pending queue so the normal retry path
        picks it up on the next scheduler sweep.

        Returns:
            The number of UNRELEASED requests that were examined.
        """
        logger = self._logger
        async with self.db_session_factory() as db:
            lifecycle = LifecycleService(db)
            unreleased = await lifecycle.get_unreleased_requests(limit=500)
            if not unreleased:
                return 0

            runtime_settings = await get_effective_settings(db)
            overseerr = OverseerrService(settings=runtime_settings)
            try:
                evaluator = UnreleasedEvaluator(db, overseerr)
                for req in unreleased:
                    try:
                        new_status = await evaluator.evaluate_and_apply(req)
                        if new_status == RequestStatus.PENDING:
                            await PendingQueueService(db).add_to_queue(req.id)
                            logger.info(
                                "Request %s transitioned UNRELEASED -> PENDING; enqueued",
                                req.id,
                            )
                    except Exception:
                        logger.exception("Re-evaluate failed for request_id=%s", req.id)
            finally:
                await overseerr.close()

            return len(unreleased)

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

        self.scheduler = AsyncIOScheduler()

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

        self.scheduler.add_job(
            self._reevaluate_unreleased,
            trigger=IntervalTrigger(hours=6),
            id="reevaluate_unreleased",
            name="Re-evaluate unreleased requests",
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

    async def trigger_reevaluate_unreleased_now(self) -> int:
        """
        Manually trigger re-evaluation of all UNRELEASED requests.

        Returns:
            Number of requests that were re-evaluated.
        """
        return await self._reevaluate_unreleased()
