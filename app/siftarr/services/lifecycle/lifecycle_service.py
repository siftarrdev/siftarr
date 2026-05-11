import logging
import time
from datetime import UTC, datetime

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.siftarr.models.activity_log import EventType
from app.siftarr.models.episode import Episode
from app.siftarr.models.request import MediaType, Request, RequestStatus
from app.siftarr.models.season import Season
from app.siftarr.services.lifecycle.activity_log_service import ActivityLogService
from app.siftarr.services.lifecycle.episode_derive import (
    derive_request_status_from_episodes,
    derive_season_status,
)

logger = logging.getLogger(__name__)


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

    _stats_cache: tuple[dict | None, float] = (None, 0.0)
    _STATS_CACHE_TTL = 30  # seconds

    def __init__(self, db: AsyncSession):
        self.db = db

    async def _load_all_episodes(self, request: Request) -> list[Episode]:
        """Load all episodes across all seasons for a TV request."""
        all_episodes: list[Episode] = []
        for season in request.seasons:
            result = await self.db.execute(select(Episode).where(Episode.season_id == season.id))
            all_episodes.extend(result.scalars().all())
        return all_episodes

    async def transition(
        self,
        request_id: int,
        new_status: RequestStatus,
        reason: str | None = None,
        *,
        commit: bool = True,
    ) -> Request | None:
        """
        Transition a request to a new status.

        Args:
            request_id: The request ID
            new_status: The new status
            reason: Optional reason for the transition

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
            if not all_episodes and new_status == RequestStatus.DOWNLOADING:
                request.status = RequestStatus.DOWNLOADING
            else:
                if new_status == RequestStatus.DENIED:
                    for ep in all_episodes:
                        ep.status = RequestStatus.DENIED
                elif new_status == RequestStatus.UNRELEASED:
                    for ep in all_episodes:
                        if ep.status == RequestStatus.PENDING:
                            ep.status = RequestStatus.UNRELEASED
                elif new_status == RequestStatus.FAILED:
                    for ep in all_episodes:
                        if ep.status in (
                            RequestStatus.PENDING,
                            RequestStatus.SEARCHING,
                            RequestStatus.DOWNLOADING,
                        ):
                            ep.status = RequestStatus.FAILED
                elif new_status == RequestStatus.DOWNLOADING:
                    for ep in all_episodes:
                        if ep.status == RequestStatus.STAGED:
                            ep.status = RequestStatus.DOWNLOADING
                # COMPLETED, PENDING, SEARCHING — no episode-level changes needed
                # Recompute season statuses and derive request status from episodes
                for season in request.seasons:
                    season_eps = [ep for ep in all_episodes if ep.season_id == season.id]
                    season.status = derive_season_status(season_eps)
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

    async def get_request_status(self, request_id: int) -> RequestStatus | None:
        """Get the current status of a request."""
        result = await self.db.execute(select(Request.status).where(Request.id == request_id))
        return result.scalar_one_or_none()

    async def get_active_requests(
        self,
        limit: int = 100,
    ) -> list[Request]:
        """Get all active requests (not completed/failed).

        For TV, includes requests where any episode is in a non-terminal
        state (SEARCHING, PENDING, UNRELEASED, STAGED, DOWNLOADING).
        """
        NON_TERMINAL = [
            RequestStatus.SEARCHING,
            RequestStatus.PENDING,
            RequestStatus.UNRELEASED,
            RequestStatus.STAGED,
            RequestStatus.DOWNLOADING,
        ]
        tv_active_ids_subq = (
            select(Season.request_id)
            .select_from(Episode)
            .join(Season, Episode.season_id == Season.id)
            .where(Episode.status.in_(NON_TERMINAL))
        ).subquery()
        result = await self.db.execute(
            select(Request)
            .where(
                or_(
                    Request.status.in_(NON_TERMINAL),
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

    async def get_requests_stats(self) -> dict:
        """Get statistics about all requests using SQL aggregates.

        For TV requests, status is derived from episodes so we count them
        separately and distribute across status buckets based on episode
        aggregates.

        Results are cached in-memory for 30 seconds to avoid repeated
        aggregate queries on every settings page load.
        """
        cached, timestamp = self.__class__._stats_cache
        if cached is not None and time.monotonic() - timestamp < self.__class__._STATS_CACHE_TTL:
            return cached

        result = await self.db.execute(
            select(Request.status, func.count()).group_by(Request.status)
        )
        rows = result.all()
        by_status = {status.value: 0 for status in RequestStatus}
        total = 0
        for status, count in rows:
            by_status[status.value] = count
            total += count

        stats = {
            "total": total,
            "by_status": by_status,
        }
        self.__class__._stats_cache = (stats, time.monotonic())
        return stats

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
                    Request.status.in_(
                        [
                            RequestStatus.UNRELEASED,
                            RequestStatus.COMPLETED,
                        ]
                    ),
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
