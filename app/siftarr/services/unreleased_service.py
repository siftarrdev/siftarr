"""Unreleased evaluator service.

Glue between the pure `release_status_service` classifier, the `OverseerrService`
detail fetcher, and the `LifecycleService` state machine. The evaluator decides
whether a request's media is currently grabbable and transitions the request
into / out of the `UNRELEASED` status accordingly.
"""

from __future__ import annotations

import logging
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.siftarr.models.episode import Episode
from app.siftarr.models.request import MediaType, Request, RequestStatus
from app.siftarr.models.season import Season
from app.siftarr.services.lifecycle_service import LifecycleService
from app.siftarr.services.overseerr_service import OverseerrService
from app.siftarr.services.release_status_service import (
    classify_movie,
    classify_tv_request,
)

__all__ = ["UnreleasedEvaluator"]

_logger = logging.getLogger(__name__)

# Statuses from which a request may be redirected into UNRELEASED.
_REDIRECTABLE_STATUSES = {
    RequestStatus.RECEIVED,
    RequestStatus.PENDING,
    RequestStatus.PARTIALLY_AVAILABLE,
    RequestStatus.SEARCHING,
}


class UnreleasedEvaluator:
    """Evaluate requests for release availability and apply state transitions."""

    def __init__(self, db: AsyncSession, overseerr: OverseerrService) -> None:
        self.db = db
        self.overseerr = overseerr
        self.lifecycle = LifecycleService(db)

    async def evaluate(self, request: Request) -> Literal["released", "unreleased"]:
        """Return a coarse 2-valued verdict for `request`.

        `"partial"` from the TV classifier is collapsed to `"released"` for
        transition purposes; see the plan for rationale.
        """
        if request.tmdb_id is None:
            _logger.debug(
                "UnreleasedEvaluator: request_id=%s has no tmdb_id; returning 'released'",
                request.id,
            )
            return "released"

        if request.media_type == MediaType.MOVIE:
            details = await self.overseerr.get_media_details("movie", request.tmdb_id)
            return classify_movie(details)

        # TV path: load local episodes and classify.
        tv_details = await self.overseerr.get_media_details("tv", request.tmdb_id)
        result = await self.db.execute(
            select(Episode)
            .join(Season, Season.id == Episode.season_id)
            .where(Season.request_id == request.id)
        )
        local_episodes = list(result.scalars().all())
        verdict = classify_tv_request(tv_details, local_episodes)
        if verdict == "partial":
            return "released"
        return verdict

    async def apply_verdict(
        self,
        request: Request,
        verdict: Literal["released", "unreleased"],
    ) -> RequestStatus | None:
        """Apply a verdict via `LifecycleService.transition`.

        Returns the new status if a transition occurred, else `None`.
        """
        current = request.status

        if verdict == "unreleased" and current in _REDIRECTABLE_STATUSES:
            updated = await self.lifecycle.transition(
                request.id, RequestStatus.UNRELEASED, reason="content not yet released"
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

    async def evaluate_and_apply(self, request: Request) -> RequestStatus | None:
        """Convenience: run `evaluate` then `apply_verdict`."""
        verdict = await self.evaluate(request)
        return await self.apply_verdict(request, verdict)


async def evaluate_imported_request(
    db: AsyncSession,
    overseerr: OverseerrService,
    request: Request,
    *,
    logger: logging.Logger | None = None,
) -> RequestStatus | None:
    """Fail-open unreleased gate for a freshly imported request."""
    active_logger = logger or _logger

    try:
        await db.refresh(request)
        new_status = await UnreleasedEvaluator(db, overseerr).evaluate_and_apply(request)
        await db.refresh(request)
        return new_status
    except Exception:
        active_logger.exception(
            "Unreleased evaluation failed for imported request_id=%s", request.id
        )
        return None
