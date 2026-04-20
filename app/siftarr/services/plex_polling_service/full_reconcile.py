"""Full-library reconcile flow."""

import asyncio
import logging

from app.siftarr.models.request import MediaType, Request, RequestStatus

from .models import (
    MediaIdentity,
    NEGATIVE_RECONCILE_STATUSES,
    ProgressCallback,
    ScanMetrics,
    ScanRunResult,
)

logger = logging.getLogger(__name__)


class FullReconcileMixin:
    async def full_reconcile_scan(
        self, on_progress: ProgressCallback | None = None
    ) -> ScanRunResult:
        """Run an authoritative full-library reconcile with guarded negative sync."""
        requests = await self.get_full_reconcile_requests()
        metrics = ScanMetrics()

        if not requests:
            logger.debug("PlexPollingService: no requests for full_reconcile_scan")
            return ScanRunResult(mode="full_reconcile_scan", metrics=metrics)

        async with self.plex.scan_cycle():
            movie_scan, tv_scan = await asyncio.gather(
                self.plex.scan_library_items("movie"),
                self.plex.scan_library_items("show"),
            )

            metrics.scanned_items = len(movie_scan.items) + len(tv_scan.items)
            metrics.skipped_on_error_items = len(movie_scan.failed_sections) + len(
                tv_scan.failed_sections
            )

            movie_presence = self._index_full_scan_items(movie_scan.items)
            tv_presence = self._index_full_scan_items(tv_scan.items)
            metrics.deduped_items = self._count_full_scan_deduped_items(
                movie_scan.items
            ) + self._count_full_scan_deduped_items(tv_scan.items)

            (
                matched_requests,
                completed_requests,
                downgraded_requests,
                skipped_requests,
            ) = await self._reconcile_requests_from_full_view(
                requests,
                movie_presence=movie_presence,
                movie_authoritative=movie_scan.authoritative,
                tv_presence=tv_presence,
                tv_authoritative=tv_scan.authoritative,
                on_progress=on_progress,
            )

        metrics.matched_requests = matched_requests
        metrics.downgraded_requests = downgraded_requests
        metrics.skipped_on_error_items += skipped_requests
        if metrics.skipped_on_error_items:
            logger.info(
                "PlexPollingService: full reconcile completed partially with guarded negative "
                "reconciliation; completed=%d matched=%d downgraded=%d skipped_on_error=%d",
                completed_requests,
                metrics.matched_requests,
                metrics.downgraded_requests,
                metrics.skipped_on_error_items,
            )
        elif metrics.downgraded_requests:
            logger.info(
                "PlexPollingService: full reconcile completed with guarded negative reconciliation; "
                "completed=%d matched=%d downgraded=%d",
                completed_requests,
                metrics.matched_requests,
                metrics.downgraded_requests,
            )
        else:
            logger.info(
                "PlexPollingService: full reconcile completed cleanly; completed=%d matched=%d",
                completed_requests,
                metrics.matched_requests,
            )
        return ScanRunResult(
            mode="full_reconcile_scan",
            completed_requests=completed_requests,
            metrics=metrics,
        )

    def _index_full_scan_items(
        self, items: tuple[dict[str, object], ...]
    ) -> dict[MediaIdentity, dict[str, object]]:
        indexed: dict[MediaIdentity, dict[str, object]] = {}
        for item in items:
            normalized_item = dict(item)
            if not self._item_has_media(normalized_item):
                continue
            for identity in self._get_recent_item_identity_candidates(normalized_item):
                indexed.setdefault(identity, normalized_item)
        return indexed

    def _count_full_scan_deduped_items(self, items: tuple[dict[str, object], ...]) -> int:
        canonical_identities: set[MediaIdentity] = set()
        counted_items = 0
        for item in items:
            normalized_item = dict(item)
            if not self._item_has_media(normalized_item):
                continue
            canonical_identity = self._get_recent_item_canonical_identity(normalized_item)
            if canonical_identity is None:
                media_type = self._get_request_media_type_for_item(normalized_item)
                if media_type is None:
                    continue
                rating_key = normalized_item.get("rating_key")
                if not rating_key:
                    continue
                canonical_identity = MediaIdentity(
                    media_type=media_type,
                    plex_rating_key=str(rating_key),
                )
            counted_items += 1
            canonical_identities.add(canonical_identity)
        return max(0, counted_items - len(canonical_identities))

    async def _reconcile_requests_from_full_view(
        self,
        requests: list[Request],
        *,
        movie_presence: dict[MediaIdentity, dict[str, object]],
        movie_authoritative: bool,
        tv_presence: dict[MediaIdentity, dict[str, object]],
        tv_authoritative: bool,
        on_progress: ProgressCallback | None,
    ) -> tuple[int, int, int, int]:
        matched_requests = 0
        completed_requests = 0
        downgraded_requests = 0
        skipped_requests = 0

        async def emit(payload: dict[str, object]) -> None:
            if on_progress is None:
                return
            result = on_progress(payload)
            if asyncio.iscoroutine(result):
                await result

        for index, req in enumerate(requests, start=1):
            await emit(
                {
                    "phase": "full_reconcile_scan",
                    "current": index,
                    "total": len(requests),
                    "title": req.title or f"Request #{req.id}",
                    "active": [],
                }
            )

            if req.media_type == MediaType.MOVIE:
                matched, completed, downgraded, skipped = await self._reconcile_movie_request(
                    req,
                    movie_presence=movie_presence,
                    authoritative=movie_authoritative,
                )
            elif req.media_type == MediaType.TV:
                matched, completed, downgraded, skipped = await self._reconcile_tv_request(
                    req,
                    tv_presence=tv_presence,
                    authoritative=tv_authoritative,
                )
            else:
                matched = completed = downgraded = skipped = 0

            matched_requests += matched
            completed_requests += completed
            downgraded_requests += downgraded
            skipped_requests += skipped

        return matched_requests, completed_requests, downgraded_requests, skipped_requests

    async def _reconcile_movie_request(
        self,
        req: Request,
        *,
        movie_presence: dict[MediaIdentity, dict[str, object]],
        authoritative: bool,
    ) -> tuple[int, int, int, int]:
        matched_item = self._find_matching_presence_item(req, movie_presence)
        if matched_item is not None:
            if req.status not in {RequestStatus.AVAILABLE, RequestStatus.COMPLETED}:
                await self._run_serialized_write(
                    self.lifecycle.transition(
                        req.id,
                        RequestStatus.COMPLETED,
                        reason="Found on Plex",
                    )
                )
                req.status = RequestStatus.COMPLETED
                return 1, 1, 0, 0
            return 1, 0, 0, 0

        if not authoritative or req.status not in NEGATIVE_RECONCILE_STATUSES:
            return 0, 0, 0, 0

        await self._run_serialized_write(
            self.lifecycle.transition(
                req.id,
                RequestStatus.PENDING,
                reason="Full Plex reconcile no longer finds this movie",
            )
        )
        req.status = RequestStatus.PENDING
        return 0, 0, 1, 0

    async def _reconcile_tv_request(
        self,
        req: Request,
        *,
        tv_presence: dict[MediaIdentity, dict[str, object]],
        authoritative: bool,
    ) -> tuple[int, int, int, int]:
        matched_show = self._find_matching_presence_item(req, tv_presence)
        if matched_show is None:
            if not authoritative or req.status not in NEGATIVE_RECONCILE_STATUSES:
                return 0, 0, 0, 0
            if not req.seasons:
                return 0, 0, 0, 1
            before_status = req.status
            await self._run_serialized_write(
                self.episode_sync.reconcile_existing_seasons_from_plex(req, req.seasons, {})
            )
            return 0, 0, int(req.status != before_status), 0

        episode_result = await self.plex.get_episode_availability_result(
            str(matched_show["rating_key"])
        )
        if not episode_result.authoritative:
            return 1, 0, 0, 1

        matched_count = 1
        before_status = req.status
        if req.seasons:
            await self._run_serialized_write(
                self.episode_sync.reconcile_existing_seasons_from_plex(
                    req,
                    req.seasons,
                    episode_result.availability,
                )
            )

        requested_episodes = self._get_requested_episodes(req)
        if requested_episodes and all(
            episode_result.availability.get(key, False) for key in requested_episodes
        ):
            return matched_count, int(req.status != before_status), 0, 0

        return matched_count, 0, int(req.status != before_status), 0

    def _find_matching_presence_item(
        self,
        req: Request,
        presence_index: dict[MediaIdentity, dict[str, object]],
    ) -> dict[str, object] | None:
        for identity in self._get_request_media_identity_candidates(req):
            item = presence_index.get(identity)
            if item is not None:
                return item
        return None
