"""Service for polling Plex to check if requested media has become available."""

import json
import logging
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.siftarr.config import get_settings
from app.siftarr.models.request import MediaType, Request, RequestStatus
from app.siftarr.models.season import Season
from app.siftarr.services.async_utils import gather_limited
from app.siftarr.services.lifecycle_service import LifecycleService
from app.siftarr.services.plex_service import PlexService

logger = logging.getLogger(__name__)

# All non-terminal statuses
NON_TERMINAL_STATUSES = [
    RequestStatus.RECEIVED,
    RequestStatus.SEARCHING,
    RequestStatus.PENDING,
    RequestStatus.PARTIALLY_AVAILABLE,
    RequestStatus.UNRELEASED,
    RequestStatus.STAGED,
    RequestStatus.DOWNLOADING,
]

type EpisodeKey = tuple[int, int]


@dataclass(frozen=True)
class PollDecision:
    """Immutable polling result produced by the read-only probe stage."""

    request_id: int
    reason: str
    requested_episode_count: int = 0
    completed_episodes: frozenset[EpisodeKey] = field(default_factory=frozenset)


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
        requests_by_id = {req.id: req for req in requests}

        async def probe(req: Request) -> PollDecision | None:
            try:
                if req.media_type == MediaType.MOVIE:
                    return await self._check_movie(req)
                if req.media_type == MediaType.TV:
                    return await self._check_tv(req)
                return None
            except Exception:
                logger.exception(
                    "PlexPollingService: error checking request_id=%s title=%s",
                    req.id,
                    req.title,
                )
                return None

        probe_results = await gather_limited(requests, self._get_concurrency_limit(), probe)

        completed = 0
        for decision in probe_results:
            if decision is None:
                continue

            req = requests_by_id.get(decision.request_id)
            if req is None:
                continue

            await self._apply_decision(req, decision)
            completed += 1

        logger.info("PlexPollingService: completed %d request(s) this cycle", completed)
        return completed

    def _get_concurrency_limit(self) -> int:
        settings = getattr(self.plex, "settings", None)
        configured = getattr(settings, "plex_sync_concurrency", None)
        if isinstance(configured, int) and configured > 0:
            return configured
        return max(1, get_settings().plex_sync_concurrency)

    async def _apply_decision(self, req: Request, decision: PollDecision) -> None:
        if decision.completed_episodes:
            logger.info(
                "PlexPollingService: TV '%s' all %d requested episode(s) available on Plex, "
                "completing request_id=%s",
                req.title,
                decision.requested_episode_count,
                req.id,
            )
            await self._update_episode_statuses(req, decision.completed_episodes)
        else:
            logger.info(
                "PlexPollingService: movie '%s' (tmdb_id=%s) found on Plex, completing request_id=%s",
                req.title,
                req.tmdb_id,
                req.id,
            )

        await self.lifecycle.transition(req.id, RequestStatus.COMPLETED, reason=decision.reason)

    async def _check_movie(self, req: Request) -> PollDecision | None:
        """Check if a movie request is available on Plex."""
        if not req.tmdb_id:
            return None

        available = await self.plex.check_movie_available(req.tmdb_id)
        if available:
            return PollDecision(request_id=req.id, reason="Found on Plex")
        return None

    async def _check_tv(self, req: Request) -> PollDecision | None:
        """Check if a TV request is fully available on Plex."""
        show = await self._find_show(req)
        if not show:
            return None

        rating_key = show["rating_key"]
        availability = await self.plex.get_episode_availability(rating_key)

        if not availability:
            return None

        # Determine which episodes were requested
        requested_episodes = self._get_requested_episodes(req)
        if not requested_episodes:
            return None

        # Check if all requested episodes are available
        all_available = all(availability.get((s, e), False) for s, e in requested_episodes)

        if all_available:
            return PollDecision(
                request_id=req.id,
                reason="All episodes found on Plex",
                requested_episode_count=len(requested_episodes),
                completed_episodes=frozenset(requested_episodes),
            )

        return None

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
        self, req: Request, completed_episodes: frozenset[EpisodeKey]
    ) -> None:
        """Update episode statuses based on Plex availability."""
        for season in req.seasons:
            for ep in season.episodes:
                key = (season.season_number, ep.episode_number)
                if key in completed_episodes and ep.status != RequestStatus.COMPLETED:
                    ep.status = RequestStatus.COMPLETED
            # If all episodes in season are completed, mark season completed too
            if season.episodes and all(
                e.status == RequestStatus.COMPLETED for e in season.episodes
            ):
                season.status = RequestStatus.COMPLETED
        await self.db.commit()
