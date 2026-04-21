import logging
from collections.abc import Iterable
from datetime import UTC, date, datetime
from typing import Literal, Protocol

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.siftarr.models.activity_log import EventType
from app.siftarr.models.request import MediaType, Request, RequestStatus
from app.siftarr.services.activity_log_service import ActivityLogService

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

    def __init__(self, db: AsyncSession):
        self.db = db

    async def transition(
        self,
        request_id: int,
        new_status: RequestStatus,
        reason: str | None = None,
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
        result = await self.db.execute(select(Request).where(Request.id == request_id))
        request = result.scalar_one_or_none()

        if not request:
            return None

        old_status = request.status
        request.status = new_status
        if reason is not None:
            request.rejection_reason = reason
        request.updated_at = datetime.now(UTC)
        await self.db.commit()
        await self.db.refresh(request)

        logger.info(
            "Request state transition: request_id=%s %s -> %s%s",
            request_id,
            old_status.value,
            new_status.value,
            f" (reason: {reason})" if reason else "",
        )

        activity_log = ActivityLogService(self.db)
        await activity_log.log(
            EventType.REQUEST_STATUS_CHANGED,
            request_id=request_id,
            details={
                "old_status": old_status.value,
                "new_status": new_status.value,
                "reason": reason,
            },
        )
        await self.db.commit()

        return request

    async def get_request_status(self, request_id: int) -> RequestStatus | None:
        """Get the current status of a request."""
        result = await self.db.execute(select(Request.status).where(Request.id == request_id))
        return result.scalar_one_or_none()

    async def get_active_requests(
        self,
        limit: int = 100,
    ) -> list[Request]:
        """Get all active requests (not completed/failed)."""
        result = await self.db.execute(
            select(Request)
            .where(
                Request.status.in_(
                    [
                        RequestStatus.SEARCHING,
                        RequestStatus.PENDING,
                        RequestStatus.UNRELEASED,
                        RequestStatus.STAGED,
                        RequestStatus.DOWNLOADING,
                    ]
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
        """Get requests by specific status."""
        result = await self.db.execute(
            select(Request)
            .where(Request.status == status)
            .order_by(Request.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def get_requests_stats(self) -> dict:
        """Get statistics about all requests using SQL aggregates."""
        result = await self.db.execute(
            select(Request.status, func.count()).group_by(Request.status)
        )
        rows = result.all()
        by_status = {status.value: 0 for status in RequestStatus}
        total = 0
        for status, count in rows:
            by_status[status.value] = count
            total += count

        return {
            "total": total,
            "by_status": by_status,
        }

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
        """Get requests that may need the Unreleased tab treatment."""
        result = await self.db.execute(
            select(Request)
            .where(
                Request.status.in_(
                    [
                        RequestStatus.UNRELEASED,
                        RequestStatus.COMPLETED,
                    ]
                )
            )
            .order_by(Request.updated_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def get_release_recheck_requests(self, limit: int = 500) -> list[Request]:
        """Get requests that should be revisited for unreleased/released state."""
        result = await self.db.execute(
            select(Request)
            .where(
                or_(
                    Request.status == RequestStatus.UNRELEASED,
                    and_(
                        Request.media_type == MediaType.TV,
                        Request.status.in_(
                            [
                                RequestStatus.COMPLETED,
                            ]
                        ),
                    ),
                )
            )
            .order_by(Request.updated_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())


_RELEASE_TYPES_AVAILABLE = {3, 4, 5}
_TV_UNAIRED_STATUSES = {"Planned", "In Production", "Pilot"}
_AVAILABLE_EPISODE_STATUSES = {RequestStatus.COMPLETED}


class EpisodeLike(Protocol):
    air_date: date | None
    status: RequestStatus


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        pass
    try:
        return datetime.fromisoformat(value).date()
    except ValueError:
        return None


def classify_movie(
    details: dict | None,
    *,
    today: date | None = None,
) -> Literal["released", "unreleased"]:
    if details is None:
        return "released"

    today = today or date.today()
    status = details.get("status")
    status_not_released = status != "Released"
    release_date = _parse_date(details.get("releaseDate"))
    release_date_missing_or_future = release_date is None or release_date > today

    has_past_avail_release = False
    releases_block = details.get("releases")
    if isinstance(releases_block, dict):
        results = releases_block.get("results")
        if isinstance(results, list):
            for country in results:
                if not isinstance(country, dict):
                    continue
                dates = country.get("release_dates")
                if not isinstance(dates, list):
                    continue
                for entry in dates:
                    if not isinstance(entry, dict):
                        continue
                    if entry.get("type") not in _RELEASE_TYPES_AVAILABLE:
                        continue
                    parsed = _parse_date(entry.get("release_date"))
                    if parsed is not None and parsed <= today:
                        has_past_avail_release = True
                        break
                if has_past_avail_release:
                    break

    if status_not_released and release_date_missing_or_future and not has_past_avail_release:
        return "unreleased"
    return "released"


def classify_tv_request(
    tv_details: dict | None,
    local_episodes: Iterable[EpisodeLike],
    *,
    today: date | None = None,
    has_empty_seasons: bool = False,
) -> Literal["released", "unreleased"]:
    if tv_details is None:
        return "released"

    today = today or date.today()
    episodes = list(local_episodes)
    next_episode = tv_details.get("nextEpisodeToAir")
    next_episode_air_date = None
    if isinstance(next_episode, dict):
        next_episode_air_date = _parse_date(
            next_episode.get("airDate") or next_episode.get("airDateUtc")
        )
    has_future_signal = has_empty_seasons or (
        next_episode_air_date is not None and next_episode_air_date > today
    )

    any_aired_locally = any(e.air_date is not None and e.air_date <= today for e in episodes)
    first_air = _parse_date(tv_details.get("firstAirDate"))
    first_air_missing_or_future = first_air is None or first_air > today
    series_status = tv_details.get("status")
    series_status_unaired = series_status in _TV_UNAIRED_STATUSES

    if (first_air_missing_or_future or series_status_unaired) and not any_aired_locally:
        return "unreleased"

    if any_aired_locally:
        aired = [e for e in episodes if e.air_date is not None and e.air_date <= today]
        all_aired_downloaded = all(e.status in _AVAILABLE_EPISODE_STATUSES for e in aired)
        has_future_or_unknown = has_future_signal or any(
            e.air_date is None or e.air_date > today for e in episodes
        )
        if all_aired_downloaded and has_future_or_unknown:
            return "unreleased"
        return "released"

    return "released"
