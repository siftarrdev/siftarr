"""Probe helpers shared by poll, targeted, and incremental flows."""

import logging
from collections.abc import Awaitable
from typing import TYPE_CHECKING, TypeVar

from app.siftarr.models.request import MediaType, Request, RequestStatus

from .models import EpisodeKey, PollDecision, ScanProbeResult

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.siftarr.services.plex_service import PlexService

T = TypeVar("T")

logger = logging.getLogger(__name__)


class ProbeMixin:
    db: "AsyncSession"
    plex: "PlexService"

    async def _run_serialized_write(self, operation: Awaitable[T]) -> T:
        raise NotImplementedError

    async def _check_movie(self, req: Request) -> PollDecision | None:
        """Check if a movie request is available on Plex."""
        if not req.tmdb_id:
            return None

        available = await self.plex.check_movie_available(req.tmdb_id)
        if available:
            return PollDecision(request_id=req.id, reason="Found on Plex")
        return None

    async def _check_movie_authoritatively(self, req: Request) -> tuple[PollDecision | None, bool]:
        """Check movie availability without collapsing transient Plex failures."""
        if not req.tmdb_id:
            return None, True

        result = await self.plex.lookup_movie_by_tmdb(req.tmdb_id)
        if result.item is None:
            return None, result.authoritative
        if self._item_has_media(result.item):
            return PollDecision(request_id=req.id, reason="Found on Plex"), True
        return None, True

    async def _check_tv(self, req: Request) -> PollDecision | None:
        """Check if a TV request is fully available on Plex."""
        result = await self._probe_tv_group((req,))
        return result.decisions[0] if result.decisions else None

    async def _find_show(self, req: Request) -> dict | None:
        """Find a show in Plex by TMDB or TVDB ID."""
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
        self, req: Request
    ) -> tuple[dict[str, object] | None, bool]:
        """Find a show while preserving inconclusive lookup semantics."""
        authoritative = True

        if req.tmdb_id:
            result = await self.plex.lookup_show_by_tmdb(req.tmdb_id)
            if result.item is not None:
                return self._item_to_lookup_dict(result.item), True
            authoritative = authoritative and result.authoritative

        if req.tvdb_id:
            result = await self.plex.lookup_show_by_tvdb(req.tvdb_id)
            if result.item is not None:
                return self._item_to_lookup_dict(result.item), True
            authoritative = authoritative and result.authoritative

        return None, authoritative

    @staticmethod
    def _item_to_lookup_dict(item: dict[str, object]) -> dict[str, object]:
        """Normalize a Plex lookup item into the shape expected by probe helpers."""
        rating_key = item.get("rating_key") or item.get("ratingKey")
        return {
            "rating_key": str(rating_key) if rating_key is not None else None,
            "title": item.get("title"),
            "guid": item.get("guid"),
            "Media": item.get("Media"),
        }

    async def _probe_request_group(self, requests: tuple[Request, ...]) -> ScanProbeResult:
        representative = requests[0]
        try:
            if representative.media_type == MediaType.MOVIE:
                return await self._probe_movie_group(requests)
            if representative.media_type == MediaType.TV:
                return await self._probe_tv_group(requests)
            return ScanProbeResult()
        except Exception:
            logger.exception(
                "PlexPollingService: error checking request_id=%s title=%s",
                representative.id,
                representative.title,
            )
            return ScanProbeResult(skipped_on_error_items=1)

    async def _probe_movie_group(
        self,
        requests: tuple[Request, ...],
        *,
        authoritative_required: bool = False,
    ) -> ScanProbeResult:
        if authoritative_required:
            decision, authoritative = await self._check_movie_authoritatively(requests[0])
            if not authoritative:
                return ScanProbeResult(skipped_on_error_items=1)
        else:
            decision = await self._check_movie(requests[0])
        if decision is None:
            return ScanProbeResult()

        return ScanProbeResult(
            decisions=tuple(
                PollDecision(request_id=req.id, reason=decision.reason) for req in requests
            ),
            matched_requests=len(requests),
        )

    async def _probe_tv_group(
        self,
        requests: tuple[Request, ...],
        show: dict[str, object] | None = None,
        *,
        authoritative_required: bool = False,
    ) -> ScanProbeResult:
        if show is None:
            if authoritative_required:
                show, authoritative = await self._find_show_authoritatively(requests[0])
                if not authoritative:
                    return ScanProbeResult(skipped_on_error_items=1)
            else:
                show = await self._find_show(requests[0])
        if not show:
            return ScanProbeResult()

        rating_key = str(show["rating_key"])
        if authoritative_required:
            episode_result = await self.plex.get_episode_availability_result(rating_key)
            if not episode_result.authoritative:
                return ScanProbeResult(skipped_on_error_items=1)
            availability = episode_result.availability
        else:
            availability = await self.plex.get_episode_availability(rating_key)
            if not availability:
                return ScanProbeResult()

        decisions: list[PollDecision] = []
        for req in requests:
            requested_episodes = self._get_requested_episodes(req)
            if not requested_episodes:
                continue

            completed_episodes = frozenset(
                (season_number, episode_number)
                for season_number, episode_number in requested_episodes
                if availability.get((season_number, episode_number), False)
            )
            if len(completed_episodes) == len(requested_episodes):
                decisions.append(
                    PollDecision(
                        request_id=req.id,
                        reason="All episodes found on Plex",
                        requested_episode_count=len(requested_episodes),
                        completed_episodes=completed_episodes,
                        episode_availability=dict(availability),
                    )
                )

        return ScanProbeResult(decisions=tuple(decisions), matched_requests=len(decisions))

    def _get_show_dict_from_recent_item(self, item: dict[str, object]) -> dict[str, object] | None:
        rating_key = item.get("rating_key")
        if not rating_key:
            return None
        return {
            "rating_key": str(rating_key),
            "title": item.get("title"),
            "guid": item.get("guid"),
            "Media": item.get("Media"),
        }

    def _item_has_media(self, item: dict[str, object]) -> bool:
        return bool(item.get("Media"))

    def _build_incremental_error_message(
        self,
        *,
        recent_error_messages: list[str],
        skipped_on_error_items: int,
    ) -> str | None:
        if recent_error_messages:
            return "; ".join(recent_error_messages)
        if skipped_on_error_items:
            return (
                "Incremental recent Plex scan had transient request probe errors; "
                "checkpoint retained"
            )
        return None

    def _get_requested_episodes(self, req: Request) -> list[EpisodeKey]:
        """Get list of (season, episode) tuples from request's seasons/episodes."""
        episodes: list[EpisodeKey] = []
        for season in req.seasons:
            for episode in season.episodes:
                episodes.append((season.season_number, episode.episode_number))
        return episodes

    async def _update_episode_statuses(
        self, req: Request, completed_episodes: frozenset[EpisodeKey]
    ) -> None:
        """Update episode statuses based on Plex availability."""
        for season in req.seasons:
            for ep in season.episodes:
                key = (season.season_number, ep.episode_number)
                if key in completed_episodes and ep.status != RequestStatus.COMPLETED:
                    ep.status = RequestStatus.COMPLETED
            if season.episodes and all(
                e.status == RequestStatus.COMPLETED for e in season.episodes
            ):
                season.status = RequestStatus.COMPLETED
        await self.db.commit()
