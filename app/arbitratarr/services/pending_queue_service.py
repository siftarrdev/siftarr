from datetime import datetime, timedelta
from typing import Optional
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.arbitratarr.models.pending_queue import PendingQueue
from app.arbitratarr.models.request import Request, RequestStatus


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
        error_message: Optional[str] = None,
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
        next_retry = datetime.utcnow() + timedelta(hours=retry_interval_hours)

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

    async def get_by_request_id(self, request_id: int) -> Optional[PendingQueue]:
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
        now = datetime.utcnow()
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
        entry.next_retry_at = datetime.utcnow() + timedelta(hours=24)
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
        """Get statistics about the pending queue."""
        result = await self.db.execute(select(PendingQueue))
        all_items = list(result.scalars().all())
        now = datetime.utcnow()

        ready = [i for i in all_items if i.next_retry_at <= now]
        waiting = [i for i in all_items if i.next_retry_at > now]

        return {
            "total_pending": len(all_items),
            "ready_for_retry": len(ready),
            "waiting_for_retry": len(waiting),
            "oldest_pending": min(i.next_retry_at for i in all_items) if all_items else None,
        }
