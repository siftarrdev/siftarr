"""Background task scheduler using APScheduler."""

import asyncio
import logging
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import cast
from uuid import uuid4

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
from app.siftarr.services.plex_scan_state_service import PlexScanStateService
from app.siftarr.services.plex_service import PlexService
from app.siftarr.services.prowlarr_service import ProwlarrService
from app.siftarr.services.qbittorrent_service import QbittorrentService
from app.siftarr.services.runtime_settings import get_effective_settings
from app.siftarr.services.tv_decision_service import TVDecisionService
from app.siftarr.services.unreleased_service import UnreleasedEvaluator

PLEX_INCREMENTAL_SYNC_JOB_NAME = "plex_recent_scan"
PLEX_FULL_RECONCILE_JOB_NAME = "plex_full_reconcile"


@dataclass(frozen=True)
class PlexJobRunResult:
    """Outcome of a manually or automatically triggered Plex scan job."""

    job_name: str
    status: str
    completed_requests: int = 0
    error: str | None = None
    lock_owner: str | None = None
    metrics_payload: dict[str, object] | None = None


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
        self._download_completion_lock = asyncio.Lock()

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
        """Compatibility wrapper that reuses the incremental Plex scan job."""
        await self._run_incremental_plex_sync_job(trigger_source="legacy_poll")

    async def _run_incremental_plex_sync_job(self, *, trigger_source: str) -> PlexJobRunResult:
        """Run the fast incremental Plex sync under a persisted job lock."""
        return await self._run_guarded_plex_scan_job(
            job_name=PLEX_INCREMENTAL_SYNC_JOB_NAME,
            trigger_source=trigger_source,
            job_label="Incremental Plex sync",
            interval_minutes_attr="plex_recent_scan_interval_minutes",
            scan_method_name="incremental_recent_scan",
        )

    async def _run_full_plex_reconcile_job(self, *, trigger_source: str) -> PlexJobRunResult:
        """Run the slower full Plex reconcile under a persisted job lock."""
        return await self._run_guarded_plex_scan_job(
            job_name=PLEX_FULL_RECONCILE_JOB_NAME,
            trigger_source=trigger_source,
            job_label="Full Plex reconcile",
            interval_minutes_attr="plex_full_reconcile_interval_minutes",
            scan_method_name="full_reconcile_scan",
        )

    async def _run_guarded_plex_scan_job(
        self,
        *,
        job_name: str,
        trigger_source: str,
        job_label: str,
        interval_minutes_attr: str,
        scan_method_name: str,
    ) -> PlexJobRunResult:
        """Run a Plex scan entry point with persisted overlap protection."""
        logger = self._logger
        async with self.db_session_factory() as db:
            runtime_settings = await get_effective_settings(db)
            state_service = PlexScanStateService(db)
            lease_duration = self._get_plex_job_lease_duration(
                runtime_settings, interval_minutes_attr=interval_minutes_attr
            )

            await state_service.recover_stale_lock(job_name)
            lock_owner = self._build_lock_owner(job_name)
            state = await state_service.acquire_lock(job_name, lock_owner, lease_duration)
            if state is None:
                current_state = await state_service.get_state(job_name)
                current_owner = current_state.lock_owner if current_state else None
                logger.info(
                    "%s skipped due to lock contention via %s; job lock already held by %s",
                    job_label,
                    trigger_source,
                    current_owner or "another worker",
                )
                return PlexJobRunResult(
                    job_name=job_name,
                    status="locked",
                    lock_owner=current_owner,
                )

            plex = PlexService(settings=runtime_settings)
            try:
                polling_service = PlexPollingService(db, plex)
                scan_method = getattr(polling_service, scan_method_name)
                scan_kwargs: dict[str, object] = {}
                if scan_method_name == "incremental_recent_scan":
                    scan_kwargs = {
                        "acquire_lock": False,
                        "previous_checkpoint_at": getattr(state, "checkpoint_at", None),
                    }
                result = await scan_method(**scan_kwargs)
            except Exception as exc:
                error_message = str(exc) or exc.__class__.__name__
                await state_service.release_lock(
                    job_name,
                    lock_owner,
                    success=False,
                    last_error=error_message,
                )
                logger.exception(
                    "Error during %s triggered via %s", job_label.lower(), trigger_source
                )
                return PlexJobRunResult(
                    job_name=job_name,
                    status="failed",
                    error=error_message,
                )
            finally:
                await plex.close()

            metrics_payload = self._build_plex_job_metrics_payload(result)
            checkpoint_at = self._extract_checkpoint_at(metrics_payload)
            clean_run = self._is_clean_plex_job_result(result)
            if clean_run:
                await state_service.release_lock(
                    job_name,
                    lock_owner,
                    success=True,
                    checkpoint_at=checkpoint_at,
                    metrics_payload=metrics_payload,
                )
            else:
                await state_service.release_lock(
                    job_name,
                    lock_owner,
                    success=False,
                    checkpoint_at=checkpoint_at,
                    metrics_payload=metrics_payload,
                    last_error=self._get_plex_job_last_error(result),
                )
            logger.info(
                "%s completed via %s; transitioned %d request(s); outcome=%s",
                job_label,
                trigger_source,
                result.completed_requests,
                self._summarize_plex_job_result(result),
            )
            return PlexJobRunResult(
                job_name=job_name,
                status="completed",
                completed_requests=result.completed_requests,
                metrics_payload=metrics_payload,
            )

    def _get_plex_job_lease_duration(
        self, runtime_settings, *, interval_minutes_attr: str
    ) -> timedelta:
        """Derive a conservative persisted lock lease from the job cadence."""
        interval_minutes = getattr(runtime_settings, interval_minutes_attr, 0)
        if not isinstance(interval_minutes, int) or interval_minutes <= 0:
            interval_minutes = 15
        return timedelta(minutes=max(interval_minutes, 15))

    def _build_lock_owner(self, job_name: str) -> str:
        """Return a unique lock owner token for this process and run."""
        return f"{job_name}:{id(self)}:{uuid4()}"

    def _build_plex_job_metrics_payload(self, result) -> dict[str, object]:
        """Serialize compact scan metrics for persisted job state."""
        return {
            "mode": result.mode,
            "completed_requests": result.completed_requests,
            "scan": result.metrics.as_dict(),
        }

    def _extract_checkpoint_at(self, metrics_payload: dict[str, object]) -> datetime | None:
        """Return the persisted checkpoint timestamp from serialized metrics."""
        raw_scan_payload = metrics_payload.get("scan")
        if not isinstance(raw_scan_payload, dict):
            return None
        scan_payload = cast("dict[str, object]", raw_scan_payload)
        raw_checkpoint_payload = scan_payload.get("checkpoint")
        if not isinstance(raw_checkpoint_payload, dict):
            return None
        checkpoint_payload = cast("dict[str, object]", raw_checkpoint_payload)
        current_checkpoint_at = checkpoint_payload.get("current_checkpoint_at")
        if not isinstance(current_checkpoint_at, str) or not current_checkpoint_at:
            return None
        try:
            return datetime.fromisoformat(current_checkpoint_at)
        except ValueError:
            return None

    def _is_clean_plex_job_result(self, result) -> bool:
        """Return whether the inner Plex scan finished cleanly."""
        clean_run = getattr(result, "clean_run", None)
        if isinstance(clean_run, bool):
            return clean_run

        metrics_payload = self._build_plex_job_metrics_payload(result)
        raw_scan_payload = metrics_payload.get("scan")
        if not isinstance(raw_scan_payload, dict):
            return True
        skipped = cast("dict[str, object]", raw_scan_payload).get("skipped_on_error_items")
        return not skipped

    def _get_plex_job_last_error(self, result) -> str | None:
        """Extract the persisted error for partial/inconclusive scheduler runs."""
        last_error = getattr(result, "last_error", None)
        return last_error if isinstance(last_error, str) and last_error else None

    def _summarize_plex_job_result(self, result) -> str:
        """Return a compact operator-facing summary for scheduler logs."""
        metrics_payload = self._build_plex_job_metrics_payload(result)
        raw_scan_payload = metrics_payload.get("scan")
        if not isinstance(raw_scan_payload, dict):
            return "unknown"
        scan_payload = cast("dict[str, object]", raw_scan_payload)

        skipped = scan_payload.get("skipped_on_error_items")
        downgraded = scan_payload.get("downgraded_requests")
        checkpoint_payload = scan_payload.get("checkpoint")
        checkpoint_advanced = None
        if isinstance(checkpoint_payload, dict):
            checkpoint_advanced = cast("dict[str, object]", checkpoint_payload).get("advanced")

        if result.mode == "incremental_recent_scan":
            if skipped:
                return "incremental partial; checkpoint retained"
            if checkpoint_advanced:
                return "incremental clean; checkpoint advanced"
            return "incremental completed"

        if result.mode == "full_reconcile_scan":
            if skipped and downgraded:
                return "full partial; guarded negative reconciliation applied selectively"
            if skipped:
                return "full partial; guarded negative reconciliation withheld"
            if downgraded:
                return "full completed with guarded negative reconciliation"
            return "full completed cleanly"

        return str(result.mode)

    async def _recheck_unreleased(self) -> None:
        """Re-evaluate TV requests whose release state may have changed."""
        logger = self._logger
        try:
            async with self.db_session_factory() as db:
                lifecycle = LifecycleService(db)
                recheck_requests = await lifecycle.get_release_recheck_requests(limit=500)
                if not recheck_requests:
                    return

                runtime_settings = await get_effective_settings(db)
                overseerr = OverseerrService(settings=runtime_settings)
                try:
                    evaluator = UnreleasedEvaluator(db, overseerr)
                    queue_service = PendingQueueService(db)
                    for request in recheck_requests:
                        new_status = await evaluator.evaluate_and_apply(request)
                        if new_status == RequestStatus.PENDING:
                            await queue_service.add_to_queue(request.id)
                    logger.info("Rechecked %d TV release-state request(s)", len(recheck_requests))
                finally:
                    await overseerr.close()
        except Exception:
            logger.exception("Error during unreleased recheck")

    async def _check_download_completion(self) -> None:
        """Poll qBittorrent for completed downloads and transition requests to COMPLETED."""
        logger = self._logger
        if self._download_completion_lock.locked():
            logger.debug("DownloadCompletionService: previous run still in progress, skipping")
            return
        async with self._download_completion_lock:
            try:
                async with self.db_session_factory() as db:
                    from app.siftarr.services.download_completion_service import (
                        DownloadCompletionService,
                    )

                    runtime_settings = await get_effective_settings(db)
                    plex = PlexService(settings=runtime_settings)
                    try:
                        qbittorrent = QbittorrentService(settings=runtime_settings)
                        plex_polling = PlexPollingService(db, plex)
                        service = DownloadCompletionService(db, qbittorrent, plex_polling)
                        completed = await service.check_downloading_requests()
                        if completed:
                            logger.info(
                                "DownloadCompletionService: completed %d request(s) this cycle",
                                completed,
                            )
                    finally:
                        await plex.close()
            except Exception:
                logger.exception("Error during download completion check")

    async def _poll_overseerr(self) -> None:
        """
        Poll Overseerr for new approved requests.

        This finds requests that were approved in Overseerr but
        don't exist in our local database yet.
        """
        logger = self._logger
        try:
            async with self.db_session_factory() as db:
                from app.siftarr.routers.settings import _import_overseerr_requests
                from app.siftarr.services.runtime_settings import get_effective_settings

                runtime_settings = await get_effective_settings(db)
                if not runtime_settings.overseerr_url or not runtime_settings.overseerr_api_key:
                    logger.debug("Overseerr not configured, skipping poll")
                    return
                synced, skipped = await _import_overseerr_requests(db, runtime_settings)
                if synced:
                    logger.info(
                        "Overseerr poll: synced %d new request(s) (%d skipped/existing)",
                        synced,
                        skipped,
                    )
                else:
                    logger.debug("Overseerr poll: no new requests found (%d skipped)", skipped)
        except Exception:
            logger.exception("Error during Overseerr background poll")

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
            self._run_incremental_plex_sync_job,
            trigger=IntervalTrigger(minutes=settings.plex_recent_scan_interval_minutes),
            kwargs={"trigger_source": "scheduler"},
            id="plex_incremental_sync",
            name="Run incremental Plex sync",
            replace_existing=True,
        )

        self.scheduler.add_job(
            self._run_full_plex_reconcile_job,
            trigger=IntervalTrigger(minutes=settings.plex_full_reconcile_interval_minutes),
            kwargs={"trigger_source": "scheduler"},
            id="plex_full_reconcile",
            name="Run full Plex reconcile",
            replace_existing=True,
        )

        self.scheduler.add_job(
            self._recheck_unreleased,
            trigger=IntervalTrigger(hours=6),
            id="recheck_unreleased",
            name="Recheck unreleased media status",
            replace_existing=True,
        )

        self.scheduler.add_job(
            self._check_download_completion,
            trigger=IntervalTrigger(seconds=30),
            id="check_download_completion",
            name="Check qBittorrent download completion",
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

    async def trigger_incremental_plex_sync_now(self) -> PlexJobRunResult:
        """Manually trigger the incremental Plex sync job."""
        return await self._run_incremental_plex_sync_job(trigger_source="manual")

    async def trigger_full_plex_reconcile_now(self) -> PlexJobRunResult:
        """Manually trigger the full Plex reconcile job."""
        return await self._run_full_plex_reconcile_job(trigger_source="manual")
