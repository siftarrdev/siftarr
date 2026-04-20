"""Public Plex polling service facade."""

import asyncio
import contextlib
import logging
from collections.abc import Awaitable
from datetime import UTC, datetime, timedelta
from typing import TypeVar

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.siftarr.config import get_settings
from app.siftarr.models.request import Request, RequestStatus
from app.siftarr.models.season import Season
from app.siftarr.services.async_utils import gather_limited
from app.siftarr.services.episode_sync_service import EpisodeSyncService
from app.siftarr.services.lifecycle_service import LifecycleService
from app.siftarr.services.plex_scan_state_service import PlexScanStateService
from app.siftarr.services.plex_service import PlexService

from .full_reconcile import FullReconcileMixin
from .identity import IdentityMixin
from .incremental import IncrementalScanMixin
from .models import (
    FULL_RECONCILE_STATUSES,
    NON_TERMINAL_STATUSES,
    MediaIdentity,
    PollDecision,
    ProgressCallback,
    ScanMetrics,
    ScanProbeResult,
    ScanRunResult,
)
from .probe import ProbeMixin
from .targeted import TargetedReconcileMixin

logger = logging.getLogger(__name__)
T = TypeVar("T")


class PlexPollingService(
    TargetedReconcileMixin,
    IncrementalScanMixin,
    FullReconcileMixin,
    ProbeMixin,
    IdentityMixin,
):
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
