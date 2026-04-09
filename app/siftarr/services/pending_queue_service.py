from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.siftarr.models.pending_queue import PendingQueue
from app.siftarr.models.request import Request, RequestStatus


class PendingQueueService:
    """
    Service for managing the persistent pending queue.

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
    ) -> PendingQueue:
        """
        Add a request to the pending queue.

        Args:
            request_id: The ID of the request to add
            retry_interval_hours: Hours until next retry attempt
            error_message: Optional error message from last attempt

        Returns:
            The created PendingQueue entry
        """
        next_retry = datetime.now(UTC) + timedelta(hours=retry_interval_hours)

        # Check if already in queue
        existing = await self.get_by_request_id(request_id)
        if existing:
            existing.retry_count += 1
            existing.next_retry_at = next_retry
            existing.last_error = error_message
            await self.db.commit()
            await self.db.refresh(existing)
            return existing

        entry = PendingQueue(
            request_id=request_id,
            next_retry_at=next_retry,
            retry_count=0,
            last_error=error_message,
        )
        self.db.add(entry)
        await self.db.commit()
        await self.db.refresh(entry)
        return entry

    async def get_by_request_id(self, request_id: int) -> PendingQueue | None:
        """Get pending queue entry by request ID."""
        result = await self.db.execute(
            select(PendingQueue).where(PendingQueue.request_id == request_id)
        )
        return result.scalar_one_or_none()

    async def get_ready_for_retry(self) -> list[PendingQueue]:
        """
        Get all pending items that are ready for retry.

        Returns items where next_retry_at <= now.
        """
        now = datetime.now(UTC)
        result = await self.db.execute(
            select(PendingQueue)
            .where(PendingQueue.next_retry_at <= now)
            .order_by(PendingQueue.next_retry_at)
        )
        return list(result.scalars().all())

    async def get_all_pending(self) -> list[PendingQueue]:
        """Get all pending items, regardless of retry time."""
        result = await self.db.execute(select(PendingQueue).order_by(PendingQueue.next_retry_at))
        return list(result.scalars().all())

    async def remove_from_queue(self, request_id: int) -> bool:
        """
        Remove a request from the pending queue.

        Call this when a request has been successfully processed.
        """
        entry = await self.get_by_request_id(request_id)
        if not entry:
            return False

        await self.db.delete(entry)
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
        entry = await self.get_by_request_id(request_id)
        if not entry:
            return False, False

        entry.retry_count += 1

        if entry.retry_count >= max_retries:
            # Mark the request as failed
            result = await self.db.execute(select(Request).where(Request.id == request_id))
            request = result.scalar_one_or_none()
            if request:
                request.status = RequestStatus.FAILED
            await self.db.delete(entry)
            await self.db.commit()
            return True, True

        # Update next retry time
        entry.next_retry_at = datetime.now(UTC) + timedelta(hours=24)
        await self.db.commit()
        return True, False

    async def update_error(self, request_id: int, error_message: str) -> bool:
        """Update the last error message for a pending item."""
        entry = await self.get_by_request_id(request_id)
        if not entry:
            return False

        entry.last_error = error_message[:500]  # Truncate
        await self.db.commit()
        return True

    async def get_queue_stats(self) -> dict:
        """Get statistics about the pending queue using SQL aggregates."""
        now = datetime.now(UTC)
        total_result = await self.db.execute(select(func.count()).select_from(PendingQueue))
        total = total_result.scalar() or 0

        ready_result = await self.db.execute(
            select(func.count()).select_from(PendingQueue).where(PendingQueue.next_retry_at <= now)
        )
        ready = ready_result.scalar() or 0

        oldest_result = await self.db.execute(select(func.min(PendingQueue.next_retry_at)))
        oldest = oldest_result.scalar()

        return {
            "total_pending": total,
            "ready_for_retry": ready,
            "waiting_for_retry": total - ready,
            "oldest_pending": oldest,
        }
