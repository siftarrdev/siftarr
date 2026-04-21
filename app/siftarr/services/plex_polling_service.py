"""Simplified Plex polling service."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, TypeVar

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.siftarr.config import get_settings
from app.siftarr.models.request import MediaType, Request, RequestStatus
from app.siftarr.models.season import Season
from app.siftarr.services.async_utils import gather_limited
from app.siftarr.services.episode_sync_service import EpisodeSyncService
from app.siftarr.services.lifecycle_service import LifecycleService
from app.siftarr.services.plex_service import PlexService, PlexTransientScanError

logger = logging.getLogger(__name__)

T = TypeVar("T")
type EpisodeKey = tuple[int, int]
ProgressCallback = Callable[[dict[str, object]], Awaitable[None] | None]

NON_TERMINAL_STATUSES = [
    RequestStatus.SEARCHING,
    RequestStatus.PENDING,
    RequestStatus.UNRELEASED,
    RequestStatus.STAGED,
    RequestStatus.DOWNLOADING,
]


@dataclass(slots=True)
class CheckRequestResult:
    request_id: int
    matched: bool = False
    available: bool = False
    status_before: RequestStatus | None = None
    status_after: RequestStatus | None = None
    reason: str | None = None


TargetedReconcileResult = CheckRequestResult


@dataclass(slots=True)
class ScanMetrics:
    scanned_items: int = 0
    matched_requests: int = 0
    skipped_on_error_items: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "scanned_items": self.scanned_items,
            "matched_requests": self.matched_requests,
            "skipped_on_error_items": self.skipped_on_error_items,
        }


@dataclass(slots=True)
class ScanRecentResult:
    completed_requests: int = 0
    metrics: ScanMetrics = field(default_factory=ScanMetrics)
    last_error: str | None = None

    @property
    def clean_run(self) -> bool:
        return self.metrics.skipped_on_error_items == 0 and self.last_error is None


@dataclass(slots=True)
class _PollDecision:
    request_id: int
    reason: str
    availability: dict[EpisodeKey, bool] | None = None


class PlexPollingService:
    """Poll Plex for active requests and recent additions."""

    def __init__(self, db: AsyncSession, plex: PlexService) -> None:
        self.db = db
        self.plex = plex
        self.lifecycle = LifecycleService(db)
        self.episode_sync = EpisodeSyncService(db, plex=plex)
        self._write_lock = asyncio.Lock()

    async def get_active_requests(self) -> list[Request]:
        result = await self.db.execute(
            select(Request)
            .where(Request.status.in_(NON_TERMINAL_STATUSES))
            .options(selectinload(Request.seasons).selectinload(Season.episodes))
        )
        return list(result.scalars().all())

    async def poll(self, on_progress: ProgressCallback | None = None) -> int:
        requests = await self.get_active_requests()
        if not requests:
            return 0

        decisions = await self._probe_requests(
            requests,
            phase="poll",
            on_progress=on_progress,
            partial_tv_match=False,
        )
        return await self._apply_decisions(requests, decisions)

    async def check_request(self, request_or_id: Request | int) -> CheckRequestResult:
        if isinstance(request_or_id, int):
            request_id = request_or_id
            req = await self._load_request(request_id)
        else:
            request_id = int(request_or_id.id)
            req = request_or_id
        if req is None:
            return CheckRequestResult(request_id=request_id)

        before_status = req.status
        decision = await self._probe_single_request(req, partial_tv_match=True)
        if decision is None:
            return CheckRequestResult(
                request_id=req.id,
                status_before=before_status,
                status_after=req.status,
            )

        await self._run_serialized_write(self._apply_decision(req, decision))
        return CheckRequestResult(
            request_id=req.id,
            matched=True,
            available=True,
            status_before=before_status,
            status_after=req.status,
            reason=decision.reason,
        )

    async def scan_recent(self, on_progress: ProgressCallback | None = None) -> ScanRecentResult:
        requests = await self.get_active_requests()
        metrics = ScanMetrics()
        if not requests:
            return ScanRecentResult(metrics=metrics)

        async with self.plex.scan_cycle():
            recent_items, errors = await self._collect_recent_items()
            metrics.scanned_items = len(recent_items)
            decisions, skipped = await self._probe_recent_requests(
                requests,
                recent_items,
                on_progress=on_progress,
            )

        metrics.matched_requests = len(decisions)
        metrics.skipped_on_error_items = len(errors) + skipped
        completed = await self._apply_decisions(requests, decisions)
        last_error = "; ".join(errors) if errors else None
        if last_error is None and skipped:
            last_error = "Recent Plex scan had request probe errors"
        return ScanRecentResult(
            completed_requests=completed,
            metrics=metrics,
            last_error=last_error,
        )

    async def _load_request(self, request_id: int) -> Request | None:
        result = await self.db.execute(
            select(Request)
            .where(Request.id == request_id)
            .options(selectinload(Request.seasons).selectinload(Season.episodes))
        )
        return result.scalar_one_or_none()

    def _get_concurrency_limit(self) -> int:
        settings = getattr(self.plex, "settings", None)
        configured = getattr(settings, "plex_sync_concurrency", None)
        if isinstance(configured, int) and configured > 0:
            return configured
        return max(1, get_settings().plex_sync_concurrency)

    async def _probe_requests(
        self,
        requests: list[Request],
        *,
        phase: str,
        on_progress: ProgressCallback | None,
        partial_tv_match: bool,
    ) -> list[_PollDecision]:
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

        async def run(req: Request) -> _PollDecision | None:
            nonlocal started, finished
            title = req.title or f"Request #{req.id}"

            async with active_lock:
                active_titles.append(title)
                started += 1
                active_snapshot = active_titles[:16]

            await emit(
                {
                    "phase": phase,
                    "current": started,
                    "total": len(requests),
                    "title": title,
                    "active": active_snapshot,
                }
            )

            try:
                return await self._probe_single_request(req, partial_tv_match=partial_tv_match)
            except Exception:
                logger.exception(
                    "PlexPollingService: error checking request_id=%s title=%s",
                    req.id,
                    req.title,
                )
                return None
            finally:
                async with active_lock:
                    with contextlib.suppress(ValueError):
                        active_titles.remove(title)
                    finished += 1
                    active_snapshot = active_titles[:16]

                await emit(
                    {
                        "phase": phase,
                        "current": finished,
                        "total": len(requests),
                        "title": title,
                        "active": active_snapshot,
                    }
                )

        results = await gather_limited(requests, self._get_concurrency_limit(), run)
        return [decision for decision in results if decision is not None]

    async def _probe_single_request(
        self,
        req: Request,
        *,
        partial_tv_match: bool,
    ) -> _PollDecision | None:
        if req.media_type == MediaType.MOVIE:
            return await self._probe_movie(req)
        if req.media_type == MediaType.TV:
            return await self._probe_tv(req, partial_tv_match=partial_tv_match)
        return None

    async def _probe_movie(self, req: Request) -> _PollDecision | None:
        if not req.tmdb_id:
            return None
        available = await self.plex.check_movie_available(req.tmdb_id)
        if not available:
            return None
        return _PollDecision(request_id=req.id, reason="Found on Plex")

    async def _probe_tv(self, req: Request, *, partial_tv_match: bool) -> _PollDecision | None:
        show = await self._find_show(req)
        if not show:
            return None

        availability = await self.plex.get_episode_availability(str(show["rating_key"]))
        requested_episodes = self._get_requested_episodes(req)
        if not requested_episodes:
            return None

        completed_episodes = [key for key in requested_episodes if availability.get(key, False)]
        if not completed_episodes:
            return None
        if not partial_tv_match and len(completed_episodes) != len(requested_episodes):
            return None

        reason = (
            "All episodes found on Plex"
            if len(completed_episodes) == len(requested_episodes)
            else "Some episodes found on Plex"
        )
        return _PollDecision(request_id=req.id, reason=reason, availability=dict(availability))

    async def _probe_recent_requests(
        self,
        requests: list[Request],
        recent_items: list[dict[str, object]],
        *,
        on_progress: ProgressCallback | None,
    ) -> tuple[list[_PollDecision], int]:
        async def emit(payload: dict[str, object]) -> None:
            if on_progress is None:
                return
            result = on_progress(payload)
            if asyncio.iscoroutine(result):
                await result

        decisions: list[_PollDecision] = []
        skipped_on_error = 0
        matched_requests = [req for req in requests if self._find_recent_item_for_request(req, recent_items)]

        for index, req in enumerate(matched_requests, start=1):
            await emit(
                {
                    "phase": "scan_recent",
                    "current": index,
                    "total": len(matched_requests),
                    "title": req.title or f"Request #{req.id}",
                    "active": [],
                }
            )

            item = self._find_recent_item_for_request(req, recent_items)
            if item is None:
                continue

            try:
                decision, skipped = await self._probe_request_from_recent_item(req, item)
            except Exception:
                logger.exception(
                    "PlexPollingService: error checking request_id=%s title=%s during recent scan",
                    req.id,
                    req.title,
                )
                skipped_on_error += 1
                continue

            skipped_on_error += skipped
            if decision is not None:
                decisions.append(decision)

        return decisions, skipped_on_error

    async def _probe_request_from_recent_item(
        self,
        req: Request,
        item: dict[str, object],
    ) -> tuple[_PollDecision | None, int]:
        if req.media_type == MediaType.MOVIE:
            if self._item_has_media(item):
                return _PollDecision(request_id=req.id, reason="Found on Plex"), 0
            if not req.tmdb_id:
                return None, 0
            result = await self.plex.lookup_movie_by_tmdb(req.tmdb_id)
            if not result.authoritative:
                return None, 1
            if result.item and self._item_has_media(result.item):
                return _PollDecision(request_id=req.id, reason="Found on Plex"), 0
            return None, 0

        rating_key = item.get("rating_key")
        if not rating_key:
            show, authoritative = await self._find_show_authoritatively(req)
            if not authoritative:
                return None, 1
            if not show:
                return None, 0
            rating_key = show.get("rating_key")

        if not rating_key:
            return None, 0

        availability_result = await self.plex.get_episode_availability_result(str(rating_key))
        if not availability_result.authoritative:
            return None, 1

        requested_episodes = self._get_requested_episodes(req)
        completed_episodes = [
            key for key in requested_episodes if availability_result.availability.get(key, False)
        ]
        if not completed_episodes:
            return None, 0

        reason = (
            "All episodes found on Plex"
            if len(completed_episodes) == len(requested_episodes)
            else "Some episodes found on Plex"
        )
        return (
            _PollDecision(
                request_id=req.id,
                reason=reason,
                availability=dict(availability_result.availability),
            ),
            0,
        )

    async def _collect_recent_items(self) -> tuple[list[dict[str, object]], list[str]]:
        items: list[dict[str, object]] = []
        errors: list[str] = []
        for media_type in ("movie", "show"):
            try:
                async for item in self.plex.iter_recently_added_items(media_type):
                    items.append(dict(item))
            except PlexTransientScanError as exc:
                logger.warning(
                    "PlexPollingService: recently-added scan for %s failed transiently: %s",
                    media_type,
                    exc,
                )
                errors.append(str(exc))
        return items, errors

    def _find_recent_item_for_request(
        self,
        req: Request,
        items: list[dict[str, object]],
    ) -> dict[str, object] | None:
        matches = [item for item in items if self._item_matches_request(item, req)]
        if not matches:
            return None
        for item in matches:
            if self._item_has_media(item):
                return item
        return matches[0]

    def _item_matches_request(self, item: dict[str, object], req: Request) -> bool:
        media_type = self._get_request_media_type_for_item(item)
        if media_type != req.media_type:
            return False

        tmdb_id, tvdb_id = self._extract_guid_ids(item)
        rating_key = item.get("rating_key")
        if req.tmdb_id is not None and req.tmdb_id == tmdb_id:
            return True
        if req.media_type == MediaType.TV and req.tvdb_id is not None and req.tvdb_id == tvdb_id:
            return True
        if getattr(req, "plex_rating_key", None) and str(rating_key) == str(req.plex_rating_key):
            return True
        return False

    async def _find_show(self, req: Request) -> dict[str, object] | None:
        if req.tmdb_id:
            show = await self.plex.get_show_by_tmdb(req.tmdb_id)
            if show:
                return show
        if req.tvdb_id:
            show = await self.plex.get_show_by_tvdb(req.tvdb_id)
            if show:
                return show
        return None

    async def _find_show_authoritatively(
        self,
        req: Request,
    ) -> tuple[dict[str, object] | None, bool]:
        authoritative = True

        if req.tmdb_id:
            result = await self.plex.lookup_show_by_tmdb(req.tmdb_id)
            if result.item is not None:
                return self._lookup_item_to_show(result.item), True
            authoritative = authoritative and result.authoritative

        if req.tvdb_id:
            result = await self.plex.lookup_show_by_tvdb(req.tvdb_id)
            if result.item is not None:
                return self._lookup_item_to_show(result.item), True
            authoritative = authoritative and result.authoritative

        return None, authoritative

    @staticmethod
    def _lookup_item_to_show(item: dict[str, object]) -> dict[str, object]:
        rating_key = item.get("rating_key") or item.get("ratingKey")
        return {
            "rating_key": str(rating_key) if rating_key is not None else None,
            "title": item.get("title"),
            "guid": item.get("guid"),
            "Media": item.get("Media"),
        }

    def _get_requested_episodes(self, req: Request) -> list[EpisodeKey]:
        episodes: list[EpisodeKey] = []
        for season in req.seasons:
            for episode in season.episodes:
                episodes.append((season.season_number, episode.episode_number))
        return episodes

    def _item_has_media(self, item: dict[str, object]) -> bool:
        return bool(item.get("Media"))

    def _extract_guid_ids(self, item: dict[str, object]) -> tuple[int | None, int | None]:
        tmdb_id: int | None = None
        tvdb_id: int | None = None
        guid_values = item.get("guids")
        for guid in guid_values if isinstance(guid_values, tuple | list) else ():
            guid_value = str(guid)
            prefix, _, raw_id = guid_value.partition("://")
            if not raw_id.isdigit():
                continue
            if prefix in {"tmdb", "com.plexapp.agents.themoviedb"} and tmdb_id is None:
                tmdb_id = int(raw_id)
            if prefix in {"tvdb", "com.plexapp.agents.thetvdb"} and tvdb_id is None:
                tvdb_id = int(raw_id)
        return tmdb_id, tvdb_id

    def _get_request_media_type_for_item(self, item: dict[str, object]) -> MediaType | None:
        item_type = item.get("type")
        if item_type == "movie":
            return MediaType.MOVIE
        if item_type == "show":
            return MediaType.TV
        return None

    async def _apply_decisions(self, requests: list[Request], decisions: list[_PollDecision]) -> int:
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

    async def _apply_decision(self, req: Request, decision: _PollDecision) -> None:
        if req.media_type == MediaType.TV and decision.availability is not None:
            await self.episode_sync.reconcile_existing_seasons_from_plex(
                req,
                req.seasons,
                decision.availability,
            )
            return

        await self.lifecycle.transition(req.id, RequestStatus.COMPLETED, reason=decision.reason)


__all__ = [
    "CheckRequestResult",
    "NON_TERMINAL_STATUSES",
    "PlexPollingService",
    "ProgressCallback",
    "ScanMetrics",
    "ScanRecentResult",
    "TargetedReconcileResult",
]
