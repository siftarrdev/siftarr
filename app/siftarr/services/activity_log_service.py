"""Service for creating and querying activity log entries."""

import json
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.siftarr.models import ActivityLog, EventType

logger = logging.getLogger(__name__)


class ActivityLogService:
    """Service for structured activity logging."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def log(
        self,
        event_type: EventType,
        request_id: int | None = None,
        details: dict | None = None,
    ) -> ActivityLog:
        """Create an activity log entry and flush (caller commits)."""
        entry = ActivityLog(
            event_type=event_type.value,
            request_id=request_id,
            details=json.dumps(details) if details is not None else None,
        )
        self.db.add(entry)
        await self.db.flush()
        logger.debug("Logged %s for request_id=%s", event_type, request_id)
        return entry

    async def get_timeline(self, request_id: int, limit: int = 100) -> list[ActivityLog]:
        """Return activity logs for a specific request, newest first."""
        stmt = (
            select(ActivityLog)
            .where(ActivityLog.request_id == request_id)
            .order_by(ActivityLog.created_at.desc())
            .limit(limit)
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def get_recent(self, limit: int = 50) -> list[ActivityLog]:
        """Return recent activity logs across all requests, newest first."""
        stmt = select(ActivityLog).order_by(ActivityLog.created_at.desc()).limit(limit)
        result = await self.db.execute(stmt)
        return list(result.scalars().all())
