"""Background task scheduler using APScheduler."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from contextlib import suppress
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from math import ceil
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.siftarr.config import get_settings
from app.siftarr.models.request import MediaType, Request, RequestStatus
from app.siftarr.services import settings_service
from app.siftarr.services.lifecycle_service import LifecycleService
from app.siftarr.services.media_helpers import extract_media_title_and_year
from app.siftarr.services.movie_decision_service import MovieDecisionService
from app.siftarr.services.overseerr_service import OverseerrService
from app.siftarr.services.pending_queue_service import PendingQueueService
from app.siftarr.services.plex_polling_service import PlexPollingService
from app.siftarr.services.plex_service import PlexService
from app.siftarr.services.prowlarr_service import ProwlarrService
from app.siftarr.services.qbittorrent_service import QbittorrentService
from app.siftarr.services.tv_decision_service import TVDecisionService
from app.siftarr.services.unreleased_service import UnreleasedEvaluator, evaluate_imported_request

PLEX_RECENT_SCAN_JOB_NAME = "plex_recent_scan"
PLEX_POLL_JOB_NAME = "plex_poll"


@dataclass(frozen=True)
class PlexJobRunResult:
    """Outcome of a manually or automatically triggered Plex scan job."""

    job_name: str
    status: str
    completed_requests: int = 0
    error: str | None = None
    lock_owner: str | None = None
    metrics_payload: dict[str, Any] | None = None


@dataclass
class PlexJobState:
    """In-memory status for a Plex scheduler job."""

    last_success: datetime | None = None
    last_run: datetime | None = None
    last_started: datetime | None = None
    locked: bool = False
    lock_owner: str | None = None
    last_error: str | None = None
    metrics_payload: dict[str, Any] | None = field(default=None)


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
        self._plex_job_state_guard = asyncio.Lock()
        self._plex_job_state: dict[str, PlexJobState] = {
            PLEX_RECENT_SCAN_JOB_NAME: PlexJobState(),
            PLEX_POLL_JOB_NAME: PlexJobState(),
        }

    def _current_time(self) -> datetime:
        return datetime.now(UTC)

    @staticmethod
    def _get_max_retry_attempts(runtime_settings: Any) -> int:
        retry_interval_hours = max(int(getattr(runtime_settings, "retry_interval_hours", 24)), 1)
        max_retry_duration_days = max(
            int(getattr(runtime_settings, "max_retry_duration_days", 7)),
            1,
        )
        return max(1, ceil((max_retry_duration_days * 24) / retry_interval_hours))

    async def _handle_pending_retry_failure(
        self,
        *,
        queue_service: PendingQueueService,
        request_id: int,
        error_message: str,
        runtime_settings: Any,
    ) -> None:
        await queue_service.update_error(request_id, error_message)
        _, max_exceeded = await queue_service.mark_retry_failed(
            request_id,
            retry_interval_hours=max(
                int(getattr(runtime_settings, "retry_interval_hours", 24)),
                1,
            ),
            max_retries=self._get_max_retry_attempts(runtime_settings),
        )

        if max_exceeded:
            self._logger.warning(
                "Request %s exceeded retry limit and was marked failed: %s",
                request_id,
                error_message,
            )
            return

        self._logger.info(
            "Request %s retry failed and was rescheduled: %s", request_id, error_message
        )

    def _get_plex_job_state(self, job_name: str) -> PlexJobState:
        state = self._plex_job_state.get(job_name)
        if state is None:
            state = PlexJobState()
            self._plex_job_state[job_name] = state
        return state

    def _build_plex_job_metrics_payload(self, result: Any) -> dict[str, Any]:
        if isinstance(result, int):
            return {"completed_requests": result}

        payload = {"completed_requests": int(getattr(result, "completed_requests", 0))}
        metrics = getattr(result, "metrics", None)
        if metrics is not None:
            payload.update(metrics.as_dict())
        return payload

    @staticmethod
    def _get_plex_completed_requests(result: Any) -> int:
        if isinstance(result, int):
            return result
        return int(getattr(result, "completed_requests", 0))

    async def get_plex_job_state_snapshot(self) -> dict[str, dict[str, Any]]:
        """Return a copy of the in-memory Plex job state."""
        async with self._plex_job_state_guard:
            return {job_name: asdict(state) for job_name, state in self._plex_job_state.items()}

    async def _run_guarded_plex_scan_job(
        self,
        *,
        job_name: str,
        trigger_source: str,
        job_log_label: str,
        runner: Callable[[], Awaitable[Any]],
    ) -> PlexJobRunResult:
        logger = self._logger
        current_task = asyncio.current_task()
        lock_owner = (
            f"{trigger_source}:{id(current_task) if current_task is not None else 'unknown'}"
        )

        async with self._plex_job_state_guard:
            state = self._get_plex_job_state(job_name)
            if state.locked:
                logger.info(
                    "%s already in progress; skipping %s trigger (owner=%s)",
                    job_log_label,
                    trigger_source,
                    state.lock_owner,
                )
                return PlexJobRunResult(
                    job_name=job_name,
                    status="locked",
                    lock_owner=state.lock_owner,
                    metrics_payload=deepcopy(state.metrics_payload),
                )

            state.locked = True
            state.lock_owner = lock_owner
            state.last_started = self._current_time()
            state.last_error = None

        try:
            result = await runner()
            metrics_payload = self._build_plex_job_metrics_payload(result)
            completed_requests = self._get_plex_completed_requests(result)
            last_error = getattr(result, "last_error", None)
            finished_at = self._current_time()

            logger.info(
                "%s completed via %s; transitioned %d request(s)",
                job_log_label,
                trigger_source,
                completed_requests,
            )

            async with self._plex_job_state_guard:
                state = self._get_plex_job_state(job_name)
                state.last_run = finished_at
                if getattr(result, "clean_run", True) and not last_error:
                    state.last_success = finished_at
                state.locked = False
                state.lock_owner = None
                state.last_error = last_error if isinstance(last_error, str) else None
                state.metrics_payload = deepcopy(metrics_payload)

            return PlexJobRunResult(
                job_name=job_name,
                status="completed",
                completed_requests=completed_requests,
                lock_owner=lock_owner,
                metrics_payload=metrics_payload,
            )
        except Exception as exc:
            error_message = str(exc) or exc.__class__.__name__
            finished_at = self._current_time()
            logger.exception("Error during %s triggered via %s", job_log_label, trigger_source)

            async with self._plex_job_state_guard:
                state = self._get_plex_job_state(job_name)
                state.last_run = finished_at
                state.locked = False
                state.lock_owner = None
                state.last_error = error_message

            return PlexJobRunResult(
                job_name=job_name,
                status="failed",
                error=error_message,
                lock_owner=lock_owner,
            )

    async def _process_pending_item(self, request: Request) -> None:
        """Process a single pending item."""
        logger = self._logger
        async with self.db_session_factory() as db:
            logger.info(
                "Processing pending request: request_id=%s title=%s media_type=%s",
                request.id,
                request.title,
                request.media_type.value,
            )

            runtime_settings = get_settings()
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
                queue_service = PendingQueueService(db)

                if result["status"] in {"completed", "downloading", "staged"}:
                    await queue_service.remove_from_queue(request.id)
                    logger.info(
                        "Request %s completed retry successfully with status=%s",
                        request.id,
                        result["status"],
                    )
                else:
                    error_message = result.get("message", "Unknown error")
                    await self._handle_pending_retry_failure(
                        queue_service=queue_service,
                        request_id=request.id,
                        error_message=error_message,
                        runtime_settings=runtime_settings,
                    )
            except Exception as e:
                logger.error("Error processing request %s: %s", request.id, e)
                queue_service = PendingQueueService(db)
                await self._handle_pending_retry_failure(
                    queue_service=queue_service,
                    request_id=request.id,
                    error_message=str(e),
                    runtime_settings=runtime_settings,
                )

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
                    logger.error("Error processing pending item %s: %s", item.id, e)

    async def _run_recent_plex_scan_job(self, *, trigger_source: str) -> PlexJobRunResult:
        """Run the recent Plex scan job."""

        async def run_scan():
            async with self.db_session_factory() as db:
                runtime_settings = get_settings()
                plex = PlexService(settings=runtime_settings)
                try:
                    polling_service = PlexPollingService(db, plex)
                    return await polling_service.scan_recent()
                finally:
                    await plex.close()

        return await self._run_guarded_plex_scan_job(
            job_name=PLEX_RECENT_SCAN_JOB_NAME,
            trigger_source=trigger_source,
            job_log_label="Recent Plex scan",
            runner=run_scan,
        )

    async def _run_plex_poll_job(self, *, trigger_source: str) -> PlexJobRunResult:
        """Run the full Plex poll job."""

        async def run_scan():
            async with self.db_session_factory() as db:
                runtime_settings = get_settings()
                plex = PlexService(settings=runtime_settings)
                try:
                    polling_service = PlexPollingService(db, plex)
                    return await polling_service.poll()
                finally:
                    await plex.close()

        return await self._run_guarded_plex_scan_job(
            job_name=PLEX_POLL_JOB_NAME,
            trigger_source=trigger_source,
            job_log_label="Plex poll",
            runner=run_scan,
        )

    async def _recheck_unreleased(self) -> None:
        """Re-evaluate TV requests whose release state may have changed."""
        logger = self._logger
        try:
            async with self.db_session_factory() as db:
                lifecycle = LifecycleService(db)
                recheck_requests = await lifecycle.get_release_recheck_requests(limit=500)
                if not recheck_requests:
                    return

                runtime_settings = get_settings()
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

                    runtime_settings = get_settings()
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
                runtime_settings = get_settings()
                if not runtime_settings.overseerr_url or not runtime_settings.overseerr_api_key:
                    logger.debug("Overseerr not configured, skipping poll")
                    return
                synced, skipped = await settings_service.import_overseerr_requests(
                    db,
                    runtime_settings,
                    overseerr_service_cls=OverseerrService,
                    plex_service_cls=PlexService,
                    evaluate_imported_request_func=evaluate_imported_request,
                    prepare_overseerr_import_func=settings_service.prepare_overseerr_import,
                    logger=logger,
                )
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

        settings = get_settings()
        self.scheduler.add_job(
            self._run_recent_plex_scan_job,
            trigger=IntervalTrigger(minutes=settings.plex_recent_scan_interval_minutes),
            kwargs={"trigger_source": "scheduler"},
            id="plex_recent_scan",
            name="Run recent Plex scan",
            replace_existing=True,
        )

        self.scheduler.add_job(
            self._run_plex_poll_job,
            trigger=IntervalTrigger(minutes=getattr(settings, "plex_poll_interval_minutes", 360)),
            kwargs={"trigger_source": "scheduler"},
            id="plex_poll",
            name="Run Plex poll",
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

    async def trigger_recent_plex_scan_now(self) -> PlexJobRunResult:
        """Manually trigger the recent Plex scan job."""
        return await self._run_recent_plex_scan_job(trigger_source="manual")

    async def trigger_plex_poll_now(self) -> PlexJobRunResult:
        """Manually trigger the Plex poll job."""
        return await self._run_plex_poll_job(trigger_source="manual")
