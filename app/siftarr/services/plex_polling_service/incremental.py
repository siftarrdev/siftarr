"""Incremental recently-added scan flow."""

import asyncio
import contextlib
import logging
from datetime import UTC, datetime
from uuid import uuid4

from app.siftarr.models.request import MediaType, Request
from app.siftarr.services.async_utils import gather_limited
from app.siftarr.services.plex_service import PlexTransientScanError

from .models import (
    MediaIdentity,
    PollDecision,
    ProgressCallback,
    RecentScanMatch,
    ScanCheckpointAdvance,
    ScanMetrics,
    ScanProbeResult,
    ScanRunResult,
)

logger = logging.getLogger(__name__)


class IncrementalScanMixin:
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

    def _get_incremental_cutoff(self, checkpoint_at: datetime | None) -> datetime | None:
        checkpoint = self._coerce_datetime(checkpoint_at)
        if checkpoint is None:
            return None
        return checkpoint - self._get_incremental_checkpoint_buffer()

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
                        for req in request_identity_index.get(identity, ()):  # pragma: no branch
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
                decisions=tuple(PollDecision(request_id=req.id, reason="Found on Plex") for req in match.requests),
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
