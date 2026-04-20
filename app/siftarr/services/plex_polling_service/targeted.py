"""Targeted request reconciliation helpers."""

from collections.abc import Awaitable
from typing import TYPE_CHECKING, TypeVar

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.siftarr.models.request import MediaType, Request
from app.siftarr.models.season import Season

from .models import PollDecision, TargetedReconcileResult

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.siftarr.services.plex_service import PlexService

T = TypeVar("T")


class TargetedReconcileMixin:
    db: "AsyncSession"
    plex: "PlexService"

    async def _run_serialized_write(self, operation: Awaitable[T]) -> T:
        raise NotImplementedError

    async def _apply_decision(self, req: Request, decision: PollDecision) -> None:
        raise NotImplementedError

    async def _check_movie_authoritatively(self, req: Request) -> tuple[PollDecision | None, bool]:
        raise NotImplementedError

    async def _check_movie(self, req: Request) -> PollDecision | None:
        raise NotImplementedError

    async def _find_show_authoritatively(
        self, req: Request
    ) -> tuple[dict[str, object] | None, bool]:
        raise NotImplementedError

    async def _find_show(self, req: Request) -> dict | None:
        raise NotImplementedError

    def _get_requested_episodes(self, req: Request) -> list[tuple[int, int]]:
        raise NotImplementedError

    async def reconcile_request(
        self,
        request_or_id: Request | int,
        *,
        authoritative_required: bool = False,
    ) -> TargetedReconcileResult:
        """Run targeted Plex reconciliation for one request outside scheduler flows."""
        request_id = int(request_or_id) if isinstance(request_or_id, int) else int(request_or_id.id)
        req = await self._load_request_for_targeted_reconcile(request_id)
        if req is None:
            return TargetedReconcileResult(request_id=request_id)

        if req.media_type == MediaType.MOVIE:
            decision, authoritative = await self._get_targeted_movie_decision(
                req,
                authoritative_required=authoritative_required,
            )
        elif req.media_type == MediaType.TV:
            decision, authoritative = await self._get_targeted_tv_decision(
                req,
                authoritative_required=authoritative_required,
            )
        else:
            decision = None
            authoritative = True

        before_status = req.status
        if decision is None:
            return TargetedReconcileResult(
                request_id=req.id,
                authoritative=authoritative,
                status_before=before_status,
                status_after=req.status,
            )

        await self._run_serialized_write(self._apply_decision(req, decision))
        return TargetedReconcileResult(
            request_id=req.id,
            matched=True,
            reconciled=True,
            authoritative=authoritative,
            status_before=before_status,
            status_after=req.status,
            reason=decision.reason,
            requested_episode_count=decision.requested_episode_count,
            completed_episodes=decision.completed_episodes,
        )

    async def _load_request_for_targeted_reconcile(self, request_id: int) -> Request | None:
        result = await self.db.execute(
            select(Request)
            .where(Request.id == request_id)
            .options(selectinload(Request.seasons).selectinload(Season.episodes))
        )
        return result.scalar_one_or_none()

    async def _get_targeted_movie_decision(
        self,
        req: Request,
        *,
        authoritative_required: bool,
    ) -> tuple[PollDecision | None, bool]:
        if authoritative_required:
            return await self._check_movie_authoritatively(req)
        return await self._check_movie(req), True

    async def _get_targeted_tv_decision(
        self,
        req: Request,
        *,
        authoritative_required: bool,
    ) -> tuple[PollDecision | None, bool]:
        show: dict[str, object] | None
        if authoritative_required:
            show, authoritative = await self._find_show_authoritatively(req)
            if not authoritative:
                return None, False
        else:
            show = await self._find_show(req)
            authoritative = True

        if not show:
            return None, authoritative

        rating_key = str(show["rating_key"])
        if authoritative_required:
            episode_result = await self.plex.get_episode_availability_result(rating_key)
            if not episode_result.authoritative:
                return None, False
            availability = episode_result.availability
        else:
            availability = await self.plex.get_episode_availability(rating_key)

        requested_episodes = self._get_requested_episodes(req)
        completed_episodes = frozenset(
            key for key in requested_episodes if availability.get(key, False)
        )
        if not completed_episodes:
            return None, True

        reason = (
            "All episodes found on Plex"
            if len(completed_episodes) == len(requested_episodes)
            else "Some episodes found on Plex"
        )
        return (
            PollDecision(
                request_id=req.id,
                reason=reason,
                requested_episode_count=len(requested_episodes),
                completed_episodes=completed_episodes,
                episode_availability=dict(availability),
            ),
            True,
        )
