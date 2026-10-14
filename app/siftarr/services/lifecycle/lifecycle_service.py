import logging
from datetime import UTC, datetime

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.siftarr.models.activity_log import EventType
from app.siftarr.models.episode import Episode
from app.siftarr.models.request import (
    AVAILABILITY_SAFE_REQUEST_STATUSES,
    NON_TERMINAL_REQUEST_STATUSES,
    MediaType,
    Request,
    RequestStatus,
)
from app.siftarr.models.season import Season
from app.siftarr.services.lifecycle.activity_log_service import ActivityLogService
from app.siftarr.services.lifecycle.episode_derive import (
    derive_request_status_from_episodes,
    derive_season_status,
)
from app.siftarr.services.releases.release_parser import cached_parse_release_coverage

logger = logging.getLogger(__name__)

# TV episode-level transitions: new status -> episode statuses it may replace.
# None means every episode is replaced.
_EPISODE_TRANSITION_SOURCES: dict[RequestStatus, frozenset[RequestStatus] | None] = {
    RequestStatus.DENIED: None,
    RequestStatus.UNRELEASED: frozenset({RequestStatus.PENDING}),
    RequestStatus.FAILED: frozenset(
        {RequestStatus.PENDING, RequestStatus.SEARCHING, RequestStatus.DOWNLOADING}
    ),
    RequestStatus.DOWNLOADING: frozenset(
        {RequestStatus.STAGED, RequestStatus.PENDING, RequestStatus.SEARCHING}
    ),
}


class LifecycleService:
    """
    Service for managing request lifecycle and status transitions.

    Status States:
    - searching: Currently querying Prowlarr
    - pending: No suitable releases found, queued for retry
    - staged: In staging awaiting approval
    - downloading: Sent to qBittorrent
    - completed: Confirmed in qBittorrent
    - failed: Max retries exceeded or error
    """

    def __init__(self, db: AsyncSession):
        self.db = db

    async def _load_all_episodes(self, request: Request) -> list[Episode]:
        """Load all episodes across all seasons for a TV request."""
        # Approval commonly transitions a single episode on a show with many
        # seasons.  Do this in one query instead of issuing one query per
        # season (the old implementation made approval latency grow with the
        # number of seasons).
        season_ids = [season.id for season in request.seasons]
        if not season_ids:
            return []
        result = await self.db.execute(
            select(Episode)
            .where(Episode.season_id.in_(season_ids))
            .order_by(Episode.season_id, Episode.episode_number)
        )
        return list(result.scalars().all())

    @staticmethod
    def _episodes_covered_by_title(
        episodes: list[Episode],
        season_number_by_id: dict[int, int],
        coverage_title: str,
    ) -> list[Episode]:
        """Filter episodes down to those covered by a release title.

        Uses the same coverage parsing as the staging service so single
        episodes, season packs, multi-season packs and complete-series
        releases all resolve to their real scope.
        """
        coverage = cached_parse_release_coverage(coverage_title)
        if coverage.is_complete_series:
            return list(episodes)
        if not coverage.season_numbers:
            # Unparseable scope — fall back to the whole request rather than
            # silently marking nothing as downloading.
            return list(episodes)

        covered_seasons = set(coverage.season_numbers)
        scoped = [ep for ep in episodes if season_number_by_id.get(ep.season_id) in covered_seasons]
        if coverage.episode_number is not None:
            scoped = [ep for ep in scoped if ep.episode_number == coverage.episode_number]
        return scoped

    async def transition(
        self,
        request_id: int,
        new_status: RequestStatus,
        reason: str | None = None,
        *,
        commit: bool = True,
        coverage_title: str | None = None,
    ) -> Request | None:
        """
        Transition a request to a new status.

        Args:
            request_id: The request ID
            new_status: The new status
            reason: Optional reason for the transition
            commit: Whether to commit the surrounding transaction
            coverage_title: Optional release title used to scope a TV
                DOWNLOADING transition to only the episodes that release
                actually covers. Ignored for movies and other statuses.

        Returns:
            Updated Request or None if not found
        """
        result = await self.db.execute(
            select(Request).options(selectinload(Request.seasons)).where(Request.id == request_id)
        )
        request = result.scalar_one_or_none()

        if not request:
            return None

        old_status = request.status
        if reason is not None:
            request.rejection_reason = reason
        request.updated_at = datetime.now(UTC)

        if request.media_type == MediaType.TV:
            # For TV, apply transition at the episode level and derive request.status
            all_episodes = await self._load_all_episodes(request)
            if not all_episodes and new_status in (RequestStatus.DOWNLOADING, RequestStatus.DENIED):
                # No episode tree to derive from yet, so apply the status directly.
                request.status = new_status
            else:
                # Statuses without an entry (COMPLETED) change no episodes.
                if new_status in _EPISODE_TRANSITION_SOURCES:
                    target_episodes = all_episodes
                    if new_status == RequestStatus.DOWNLOADING and coverage_title:
                        season_number_by_id = {
                            season.id: season.season_number for season in request.seasons
                        }
                        target_episodes = self._episodes_covered_by_title(
                            all_episodes, season_number_by_id, coverage_title
                        )
                    allowed_from = _EPISODE_TRANSITION_SOURCES[new_status]
                    for ep in target_episodes:
                        if allowed_from is None or ep.status in allowed_from:
                            ep.status = new_status
                # Recompute season statuses and derive request status from episodes
                episodes_by_season: dict[int, list[Episode]] = {}
                for episode in all_episodes:
                    episodes_by_season.setdefault(episode.season_id, []).append(episode)
                for season in request.seasons:
                    season.status = derive_season_status(episodes_by_season.get(season.id, []))
                request.status = derive_request_status_from_episodes(all_episodes)
        else:
            request.status = new_status

        request.updated_at = datetime.now(UTC)
        await self.db.flush()

        logger.info(
            "Request state transition: request_id=%s %s -> %s%s",
            request_id,
            old_status.value if old_status else "None",
            new_status.value,
            f" (reason: {reason})" if reason else "",
        )

        activity_log = ActivityLogService(self.db)
        await activity_log.log(
            EventType.REQUEST_STATUS_CHANGED,
            request_id=request_id,
            details={
                "old_status": old_status.value if old_status else "None",
                "new_status": new_status.value,
                "reason": reason,
            },
        )
        if commit:
            await self.db.commit()
            await self.db.refresh(request)

        return request

    async def get_active_requests(
        self,
        limit: int = 100,
    ) -> list[Request]:
        """Get all active requests (not completed/failed).

        For TV, includes requests where any episode is in a non-terminal
        state (SEARCHING, PENDING, UNRELEASED, STAGED, DOWNLOADING).
        """
        tv_active_ids_subq = (
            select(Season.request_id)
            .select_from(Episode)
            .join(Season, Episode.season_id == Season.id)
            .where(Episode.status.in_(NON_TERMINAL_REQUEST_STATUSES))
        ).subquery()
        result = await self.db.execute(
            select(Request)
            .where(
                or_(
                    Request.status.in_(NON_TERMINAL_REQUEST_STATUSES),
                    and_(
                        Request.media_type == MediaType.TV,
                        Request.id.in_(select(tv_active_ids_subq.c.request_id)),
                    ),
                )
            )
            .order_by(Request.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def get_requests_by_status(
        self,
        status: RequestStatus,
        limit: int = 100,
    ) -> list[Request]:
        """Get requests by specific status.

        For TV, status is derived from episodes so we include TV requests
        alongside the exact Request.status match; callers may further
        refine based on derived episode state.
        """
        result = await self.db.execute(
            select(Request)
            .where(
                or_(
                    Request.status == status,
                    Request.media_type == MediaType.TV,
                )
            )
            .order_by(Request.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def update_request_metadata(
        self,
        request_id: int,
        title: str | None = None,
        year: int | None = None,
        overview: str | None = None,
    ) -> Request | None:
        """Update request metadata (title, year, etc.)."""
        result = await self.db.execute(select(Request).where(Request.id == request_id))
        request = result.scalar_one_or_none()

        if not request:
            return None

        if title is not None:
            request.title = title
        if year is not None:
            request.year = year

        request.updated_at = datetime.now(UTC)
        await self.db.commit()
        await self.db.refresh(request)

        return request

    async def get_unreleased_requests(self, limit: int = 500) -> list[Request]:
        """Get requests that may need the Unreleased tab treatment.

        Includes TV requests — their unreleased state is episode-derived.
        """
        result = await self.db.execute(
            select(Request)
            .where(
                or_(
                    Request.status.in_(AVAILABILITY_SAFE_REQUEST_STATUSES),
                    Request.media_type == MediaType.TV,
                )
            )
            .order_by(Request.updated_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def get_release_recheck_requests(self, limit: int = 500) -> list[Request]:
        """Get requests that should be revisited for unreleased/released state.

        Includes TV requests (their status is episode-derived) so they
        are rechecked alongside movie requests.
        """
        result = await self.db.execute(
            select(Request)
            .where(
                or_(
                    Request.status == RequestStatus.UNRELEASED,
                    Request.media_type == MediaType.TV,
                )
            )
            .order_by(Request.updated_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())
