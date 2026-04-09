import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.siftarr.models.request import Request, RequestStatus

logger = logging.getLogger(__name__)


class LifecycleService:
    """
    Service for managing request lifecycle and status transitions.

    Status States:
    - received: From Overseerr webhook
    - searching: Currently querying Prowlarr
    - pending: No suitable releases found, queued for retry
    - staged: In staging awaiting approval
    - downloading: Sent to qBittorrent
    - completed: Confirmed in qBittorrent
    - failed: Max retries exceeded or error
    """

    VALID_TRANSITIONS: dict[RequestStatus, list[RequestStatus]] = {
        RequestStatus.RECEIVED: [RequestStatus.SEARCHING, RequestStatus.FAILED],
        RequestStatus.SEARCHING: [
            RequestStatus.PENDING,
            RequestStatus.STAGED,
            RequestStatus.DOWNLOADING,
            RequestStatus.COMPLETED,
            RequestStatus.FAILED,
        ],
        RequestStatus.PENDING: [
            RequestStatus.SEARCHING,
            RequestStatus.STAGED,
            RequestStatus.DOWNLOADING,
            RequestStatus.COMPLETED,
            RequestStatus.FAILED,
        ],
        RequestStatus.STAGED: [
            RequestStatus.DOWNLOADING,
            RequestStatus.PENDING,
            RequestStatus.FAILED,
        ],
        RequestStatus.DOWNLOADING: [
            RequestStatus.COMPLETED,
            RequestStatus.FAILED,
        ],
        RequestStatus.COMPLETED: [],  # Terminal state
        RequestStatus.FAILED: [],  # Terminal state
    }

    def __init__(self, db: AsyncSession):
        self.db = db

    def can_transition(self, current: RequestStatus, new: RequestStatus) -> bool:
        """Check if a status transition is valid."""
        return new in self.VALID_TRANSITIONS.get(current, [])

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
            Updated Request or None if transition invalid
        """
        result = await self.db.execute(select(Request).where(Request.id == request_id))
        request = result.scalar_one_or_none()

        if not request:
            return None

        old_status = request.status
        if not self.can_transition(old_status, new_status):
            raise ValueError(f"Invalid transition from {old_status} to {new_status}")

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
                        RequestStatus.RECEIVED,
                        RequestStatus.SEARCHING,
                        RequestStatus.PENDING,
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
        """Get statistics about all requests."""
        result = await self.db.execute(select(Request))
        requests = list(result.scalars().all())

        stats = {
            "total": len(requests),
            "by_status": {},
        }

        for status in RequestStatus:
            stats["by_status"][status.value] = sum(1 for r in requests if r.status == status)

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

    async def mark_as_staged(self, request_id: int) -> Request | None:
        """Convenience method to mark a request as staged."""
        return await self.transition(request_id, RequestStatus.STAGED)

    async def mark_as_downloading(self, request_id: int) -> Request | None:
        """Convenience method to mark a request as downloading."""
        return await self.transition(request_id, RequestStatus.DOWNLOADING)

    async def mark_as_completed(self, request_id: int) -> Request | None:
        """Convenience method to mark a request as completed."""
        return await self.transition(request_id, RequestStatus.COMPLETED)

    async def mark_as_failed(
        self,
        request_id: int,
        reason: str | None = None,
    ) -> Request | None:
        """Convenience method to mark a request as failed."""
        return await self.transition(request_id, RequestStatus.FAILED, reason)

    async def mark_as_pending(self, request_id: int) -> Request | None:
        """Convenience method to mark a request as pending."""
        return await self.transition(request_id, RequestStatus.PENDING)
