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
from app.siftarr.services.lifecycle_service import LifecycleService
from app.siftarr.services.overseerr_service import OverseerrService
from app.siftarr.services.release_status_service import (
    EpisodeLike,
    classify_movie,
    classify_tv_request,
)

_logger = logging.getLogger(__name__)

_REDIRECTABLE_STATUSES = {
    RequestStatus.RECEIVED,
    RequestStatus.PENDING,
    RequestStatus.PARTIALLY_AVAILABLE,
    RequestStatus.SEARCHING,
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

        return classify_request_release_verdict(
            request,
            media_details=media_details,
            local_episodes=resolved_local_episodes,
        )

    async def apply_verdict(
        self,
        request: Request,
        verdict: Literal["released", "unreleased"],
    ) -> RequestStatus | None:
        current = request.status

        if verdict == "unreleased" and current in _REDIRECTABLE_STATUSES:
            updated = await self.lifecycle.transition(
                request.id,
                RequestStatus.UNRELEASED,
                reason="content not yet released",
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


def classify_request_release_verdict(
    request: Request,
    *,
    media_details: dict | None,
    local_episodes: Iterable[EpisodeLike] | None = None,
) -> Literal["released", "unreleased"]:
    """Classify release state using already-fetched Overseerr details when available."""
    if request.tmdb_id is None:
        return "released"

    if request.media_type == MediaType.MOVIE:
        return classify_movie(media_details)

    verdict = classify_tv_request(media_details, local_episodes or ())
    if verdict == "partial":
        return "released"
    return verdict


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
