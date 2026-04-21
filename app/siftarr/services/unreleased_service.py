"""Unreleased evaluator service."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.siftarr.models.episode import Episode
from app.siftarr.models.request import MediaType, Request, RequestStatus
from app.siftarr.models.season import Season
from app.siftarr.services.lifecycle_service import (
    EpisodeLike,
    LifecycleService,
    is_unreleased,
)
from app.siftarr.services.overseerr_service import OverseerrService

_logger = logging.getLogger(__name__)

_REDIRECTABLE_STATUSES = {
    RequestStatus.PENDING,
    RequestStatus.SEARCHING,
    RequestStatus.COMPLETED,  # Allow ongoing TV series to be re-classified as unreleased
}


class UnreleasedEvaluator:
    def __init__(self, db: AsyncSession, overseerr: OverseerrService) -> None:
        self.db = db
        self.overseerr = overseerr
        self.lifecycle = LifecycleService(db)

    async def evaluate(
        self,
        request: Request,
        *,
        prefetched_media_details: dict | None = None,
        local_episodes: Iterable[EpisodeLike] | None = None,
    ) -> Literal["released", "unreleased"]:
        media_details = prefetched_media_details
        if request.tmdb_id is not None and media_details is None:
            media_type = "movie" if request.media_type == MediaType.MOVIE else "tv"
            media_details = await self.overseerr.get_media_details(media_type, request.tmdb_id)

        resolved_local_episodes = local_episodes
        if request.media_type == MediaType.TV and resolved_local_episodes is None:
            result = await self.db.execute(
                select(Episode)
                .join(Season, Season.id == Episode.season_id)
                .where(Season.request_id == request.id)
            )
            resolved_local_episodes = list(result.scalars().all())

        has_empty_seasons = False
        if request.media_type == MediaType.TV:
            seasons_result = await self.db.execute(
                select(Season).where(Season.request_id == request.id)
            )
            all_seasons = list(seasons_result.scalars().all())
            db_episodes: list[Episode] = [
                ep for ep in (resolved_local_episodes or []) if isinstance(ep, Episode)
            ]
            season_ids_with_episodes = {ep.season_id for ep in db_episodes}
            has_empty_seasons = any(s.id not in season_ids_with_episodes for s in all_seasons)

        return (
            "unreleased"
            if is_unreleased(
                request,
                media_details=media_details,
                local_episodes=resolved_local_episodes or (),
                has_empty_seasons=has_empty_seasons,
            )
            else "released"
        )

    async def apply_verdict(
        self,
        request: Request,
        verdict: Literal["released", "unreleased"],
    ) -> RequestStatus | None:
        current = request.status

        if verdict == "unreleased" and current in _REDIRECTABLE_STATUSES:
            _logger.info(
                "UnreleasedEvaluator: reclassifying request_id=%s title=%s from %s to unreleased",
                request.id,
                request.title,
                current.value,
            )
            updated = await self.lifecycle.transition(
                request.id,
                RequestStatus.UNRELEASED,
                reason="reclassified to unreleased after release-status recheck",
            )
            if updated is not None:
                return RequestStatus.UNRELEASED
            return None

        if verdict == "released" and current == RequestStatus.UNRELEASED:
            updated = await self.lifecycle.transition(request.id, RequestStatus.PENDING)
            if updated is not None:
                return RequestStatus.PENDING
            return None

        return None

    async def evaluate_and_apply(
        self,
        request: Request,
        *,
        prefetched_media_details: dict | None = None,
        local_episodes: Iterable[EpisodeLike] | None = None,
    ) -> RequestStatus | None:
        verdict = await self.evaluate(
            request,
            prefetched_media_details=prefetched_media_details,
            local_episodes=local_episodes,
        )
        return await self.apply_verdict(request, verdict)

async def evaluate_imported_request(
    db: AsyncSession,
    overseerr: OverseerrService,
    request: Request,
    *,
    logger: logging.Logger | None = None,
    prefetched_media_details: dict | None = None,
    local_episodes: Iterable[EpisodeLike] | None = None,
) -> RequestStatus | None:
    active_logger = logger or _logger
    try:
        await db.refresh(request)
        new_status = await UnreleasedEvaluator(db, overseerr).evaluate_and_apply(
            request,
            prefetched_media_details=prefetched_media_details,
            local_episodes=local_episodes,
        )
        await db.refresh(request)
        return new_status
    except Exception:
        active_logger.exception(
            "Unreleased evaluation failed for imported request_id=%s", request.id
        )
        return None
