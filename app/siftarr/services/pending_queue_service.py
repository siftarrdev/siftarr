from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.siftarr.models.request import Request, RequestStatus


class PendingQueueService:
    """
    Service for managing the persistent pending queue directly on Request rows.

    The pending queue stores items that need to be retried because
    no releases passed the rule engine at initial search.
    """

    def __init__(self, db: AsyncSession):
        self.db = db

    async def add_to_queue(
        self,
        request_id: int,
        retry_interval_hours: int = 24,
        error_message: str | None = None,
    ) -> Request | None:
        """
        Add a request to the pending queue.

        Args:
            request_id: The ID of the request to add
            retry_interval_hours: Hours until next retry attempt
            error_message: Optional error message from last attempt

        Returns:
            The updated Request or None if not found
        """
        result = await self.db.execute(select(Request).where(Request.id == request_id))
        request = result.scalar_one_or_none()
        if not request:
            return None

        request.next_retry_at = datetime.now(UTC) + timedelta(hours=retry_interval_hours)
        request.rejection_reason = error_message[:500] if error_message else None
        await self.db.commit()
        await self.db.refresh(request)
        return request

    async def get_by_request_id(self, request_id: int) -> Request | None:
        """Get pending queue entry by request ID."""
        result = await self.db.execute(
            select(Request)
            .where(Request.id == request_id)
            .where(Request.next_retry_at.is_not(None))
        )
        return result.scalar_one_or_none()

    async def get_ready_for_retry(self) -> list[Request]:
        """
        Get all pending items that are ready for retry.

        Returns items where next_retry_at <= now.
        """
        now = datetime.now(UTC)
        result = await self.db.execute(
            select(Request)
            .where(Request.next_retry_at.is_not(None))
            .where(Request.next_retry_at <= now)
            .order_by(Request.next_retry_at)
        )
        return list(result.scalars().all())

    async def get_all_pending(self) -> list[Request]:
        """Get all pending items, regardless of retry time."""
        result = await self.db.execute(
            select(Request)
            .where(Request.next_retry_at.is_not(None))
            .order_by(Request.next_retry_at)
        )
        return list(result.scalars().all())

    async def remove_from_queue(self, request_id: int) -> bool:
        """
        Remove a request from the pending queue.

        Call this when a request has been successfully processed.
        """
        result = await self.db.execute(select(Request).where(Request.id == request_id))
        request = result.scalar_one_or_none()
        if not request or request.next_retry_at is None:
            return False

        request.next_retry_at = None
        request.retry_count = 0
        await self.db.commit()
        return True

    async def mark_retry_failed(
        self,
        request_id: int,
        max_retries: int = 7,
    ) -> tuple[bool, bool]:
        """
        Mark a retry as failed and check if max retries exceeded.

        Args:
            request_id: The request ID
            max_retries: Maximum number of retries before marking as failed

        Returns:
            Tuple of (updated, max_exceeded)
        """
        result = await self.db.execute(
            select(Request)
            .where(Request.id == request_id)
            .where(Request.next_retry_at.is_not(None))
        )
        request = result.scalar_one_or_none()
        if not request:
            return False, False

        request.retry_count += 1

        if request.retry_count >= max_retries:
            request.status = RequestStatus.FAILED
            request.next_retry_at = None
            request.retry_count = 0
            await self.db.commit()
            return True, True

        request.next_retry_at = datetime.now(UTC) + timedelta(hours=24)
        await self.db.commit()
        return True, False

    async def update_error(self, request_id: int, error_message: str) -> bool:
        """Update the last error message for a pending item."""
        result = await self.db.execute(
            select(Request)
            .where(Request.id == request_id)
            .where(Request.next_retry_at.is_not(None))
        )
        request = result.scalar_one_or_none()
        if not request:
            return False

        request.rejection_reason = error_message[:500]
        await self.db.commit()
        return True

    async def get_queue_stats(self) -> dict:
        """Get statistics about the pending queue using SQL aggregates."""
        now = datetime.now(UTC)
        total_result = await self.db.execute(
            select(func.count()).select_from(Request).where(Request.next_retry_at.is_not(None))
        )
        total = total_result.scalar() or 0

        ready_result = await self.db.execute(
            select(func.count())
            .select_from(Request)
            .where(Request.next_retry_at.is_not(None))
            .where(Request.next_retry_at <= now)
        )
        ready = ready_result.scalar() or 0

        oldest_result = await self.db.execute(
            select(func.min(Request.next_retry_at)).where(Request.next_retry_at.is_not(None))
        )
        oldest = oldest_result.scalar()

        return {
            "total_pending": total,
            "ready_for_retry": ready,
            "waiting_for_retry": total - ready,
            "oldest_pending": oldest,
        }
