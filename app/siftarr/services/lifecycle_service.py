import logging
from datetime import UTC, datetime

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
        await self.db.flush()

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
