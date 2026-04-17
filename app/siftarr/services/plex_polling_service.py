"""Service for polling Plex to check if requested media has become available."""

import json
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.siftarr.models.request import MediaType, Request, RequestStatus
from app.siftarr.models.season import Season
from app.siftarr.services.lifecycle_service import LifecycleService
from app.siftarr.services.plex_service import PlexService

logger = logging.getLogger(__name__)

# All non-terminal statuses
NON_TERMINAL_STATUSES = [
    RequestStatus.RECEIVED,
    RequestStatus.SEARCHING,
    RequestStatus.PENDING,
    RequestStatus.UNRELEASED,
    RequestStatus.STAGED,
    RequestStatus.DOWNLOADING,
]


class PlexPollingService:
    """Polls Plex to check if requested media has become available."""

    def __init__(self, db: AsyncSession, plex: PlexService) -> None:
        self.db = db
        self.plex = plex
        self.lifecycle = LifecycleService(db)

    async def poll(self) -> int:
        """Check all active requests against Plex availability.

        Returns:
            Number of requests transitioned to COMPLETED.
        """
        result = await self.db.execute(
            select(Request)
            .where(Request.status.in_(NON_TERMINAL_STATUSES))
            .options(selectinload(Request.seasons).selectinload(Season.episodes))
        )
        requests = list(result.scalars().all())

        if not requests:
            logger.debug("PlexPollingService: no active requests to poll")
            return 0

        logger.info("PlexPollingService: polling %d active request(s)", len(requests))
        completed = 0

        for req in requests:
            try:
                if req.media_type == MediaType.MOVIE:
                    if await self._check_movie(req):
                        completed += 1
                elif req.media_type == MediaType.TV and await self._check_tv(req):
                    completed += 1
            except Exception:
                logger.exception(
                    "PlexPollingService: error checking request_id=%s title=%s",
                    req.id,
                    req.title,
                )

        logger.info("PlexPollingService: completed %d request(s) this cycle", completed)
        return completed

    async def _check_movie(self, req: Request) -> bool:
        """Check if a movie request is available on Plex."""
        if not req.tmdb_id:
            return False

        available = await self.plex.check_movie_available(req.tmdb_id)
        if available:
            logger.info(
                "PlexPollingService: movie '%s' (tmdb_id=%s) found on Plex, completing request_id=%s",
                req.title,
                req.tmdb_id,
                req.id,
            )
            await self.lifecycle.transition(req.id, RequestStatus.COMPLETED, reason="Found on Plex")
            return True
        return False

    async def _check_tv(self, req: Request) -> bool:
        """Check if a TV request is fully available on Plex."""
        show = await self._find_show(req)
        if not show:
            return False

        rating_key = show["rating_key"]
        availability = await self.plex.get_episode_availability(rating_key)

        if not availability:
            return False

        # Determine which episodes were requested
        requested_episodes = self._get_requested_episodes(req)
        if not requested_episodes:
            return False

        # Check if all requested episodes are available
        all_available = all(availability.get((s, e), False) for s, e in requested_episodes)

        if all_available:
            logger.info(
                "PlexPollingService: TV '%s' all %d requested episode(s) available on Plex, "
                "completing request_id=%s",
                req.title,
                len(requested_episodes),
                req.id,
            )
            # Update individual episode statuses
            await self._update_episode_statuses(req, availability)
            await self.lifecycle.transition(
                req.id, RequestStatus.COMPLETED, reason="All episodes found on Plex"
            )
            return True

        return False

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

    def _get_requested_episodes(self, req: Request) -> list[tuple[int, int]]:
        """Get list of (season, episode) tuples from request's seasons/episodes."""
        episodes: list[tuple[int, int]] = []

        # Use the ORM relationships if loaded
        if req.seasons:
            for season in req.seasons:
                for ep in season.episodes:
                    episodes.append((season.season_number, ep.episode_number))
            return episodes

        # Fallback: parse requested_seasons + requested_episodes JSON strings
        if req.requested_episodes:
            try:
                ep_list = json.loads(req.requested_episodes)
                # Format: list of {"season": N, "episode": N}
                for item in ep_list:
                    if isinstance(item, dict) and "season" in item and "episode" in item:
                        episodes.append((item["season"], item["episode"]))
            except (json.JSONDecodeError, TypeError):
                pass

        return episodes

    async def _update_episode_statuses(
        self, req: Request, availability: dict[tuple[int, int], bool]
    ) -> None:
        """Update episode statuses based on Plex availability."""
        for season in req.seasons:
            for ep in season.episodes:
                key = (season.season_number, ep.episode_number)
                if availability.get(key, False) and ep.status != RequestStatus.COMPLETED:
                    ep.status = RequestStatus.COMPLETED
            # If all episodes in season are completed, mark season completed too
            if season.episodes and all(
                e.status == RequestStatus.COMPLETED for e in season.episodes
            ):
                season.status = RequestStatus.COMPLETED
        await self.db.commit()
