"""Background task scheduler using APScheduler."""

from contextlib import suppress

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import select

from app.siftarr.models.pending_queue import PendingQueue
from app.siftarr.models.request import MediaType, Request
from app.siftarr.services.lifecycle_service import LifecycleService
from app.siftarr.services.media_helpers import extract_media_title_and_year
from app.siftarr.services.movie_decision_service import MovieDecisionService
from app.siftarr.services.overseerr_service import OverseerrService
from app.siftarr.services.pending_queue_service import PendingQueueService
from app.siftarr.services.prowlarr_service import ProwlarrService
from app.siftarr.services.qbittorrent_service import QbittorrentService
from app.siftarr.services.runtime_settings import get_effective_settings
from app.siftarr.services.tv_decision_service import TVDecisionService


class SchedulerService:
    """
    Background task scheduler using APScheduler.

    Jobs:
    - Daily retry of pending queue items
    - Polling Overseerr for new approved requests
    """

    def __init__(self, db_session_factory) -> None:
        """
        Initialize scheduler service.

        Args:
            db_session_factory: Callable that returns an AsyncSession
        """
        self.db_session_factory = db_session_factory
        self.scheduler: AsyncIOScheduler | None = None

    async def _process_pending_item(self, pending_item: PendingQueue) -> None:
        """Process a single pending item."""
        async with self.db_session_factory() as db:
            # Get the request
            result = await db.execute(
                select(Request).where(Request.id == pending_item.request_id),
            )
            request = result.scalar_one_or_none()

            if not request:
                # Request was deleted, remove from queue
                await db.delete(pending_item)
                await db.commit()
                return

            runtime_settings = await get_effective_settings(db)
            prowlarr = ProwlarrService(settings=runtime_settings)
            qbittorrent = QbittorrentService(settings=runtime_settings)

            # Backfill year if missing (e.g. Overseerr was unreachable at creation time)
            if request.year is None and (request.tmdb_id or request.tvdb_id):
                overseerr = OverseerrService(settings=runtime_settings)
                try:
                    media_type_for_api = "movie" if request.media_type == MediaType.MOVIE else "tv"
                    media_id = request.tmdb_id or request.tvdb_id
                    title, year = await extract_media_title_and_year(
                        overseerr, media_type_for_api, media_id
                    )
                    if year is not None:
                        lifecycle = LifecycleService(db)
                        await lifecycle.update_request_metadata(request.id, year=year)
                        await db.refresh(request)
                except Exception:
                    pass
                finally:
                    await overseerr.close()

            # Create decision service based on media type
            if request.media_type == MediaType.TV:
                decision_service = TVDecisionService(db, prowlarr, qbittorrent)
            else:
                decision_service = MovieDecisionService(db, prowlarr, qbittorrent)

            try:
                result = await decision_service.process_request(request.id)

                if result["status"] == "completed":
                    # Success - remove from pending queue
                    queue_service = PendingQueueService(db)
                    await queue_service.remove_from_queue(request.id)
                else:
                    # Still pending - update retry info
                    queue_service = PendingQueueService(db)
                    await queue_service.update_error(
                        request.id,
                        result.get("message", "Unknown error"),
                    )
            except Exception as e:
                # Error during processing
                queue_service = PendingQueueService(db)
                await queue_service.update_error(request.id, str(e))

    async def _retry_pending_jobs(self) -> None:
        """Daily job to retry pending items."""
        async with self.db_session_factory() as db:
            queue_service = PendingQueueService(db)
            pending_items = await queue_service.get_ready_for_retry()

            if not pending_items:
                return

            print(f"Processing {len(pending_items)} pending items...")

            for item in pending_items:
                try:
                    await self._process_pending_item(item)
                except Exception as e:
                    print(f"Error processing pending item {item.request_id}: {e}")

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
        if self.scheduler is not None:
            return

        self.scheduler = AsyncIOScheduler()

        # Daily retry job (every 24 hours)
        self.scheduler.add_job(
            self._retry_pending_jobs,
            trigger=IntervalTrigger(hours=24),
            id="retry_pending",
            name="Retry pending queue items",
            replace_existing=True,
        )

        # Overseerr polling (every hour)
        self.scheduler.add_job(
            self._poll_overseerr,
            trigger=IntervalTrigger(hours=1),
            id="poll_overseerr",
            name="Poll Overseerr for new requests",
            replace_existing=True,
        )

        self.scheduler.start()
        print("Background scheduler started")

    def stop(self) -> None:
        """Stop the background scheduler."""
        if self.scheduler is not None:
            self.scheduler.shutdown()
            self.scheduler = None
            print("Background scheduler stopped")

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
