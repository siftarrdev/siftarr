"""Activity log model for tracking request lifecycle events."""

import enum
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.siftarr.models._base import Base  # noqa: PLC0414


def _utc_now() -> datetime:
    """Return current UTC time as timezone-aware datetime."""
    return datetime.now(timezone.utc)  # noqa: UP017


class EventType(enum.StrEnum):
    SEARCH_STARTED = "search_started"
    SEARCH_COMPLETED = "search_completed"
    RULE_EVALUATION = "rule_evaluation"
    RELEASE_STAGED = "release_staged"
    RELEASE_APPROVED = "release_approved"
    DOWNLOAD_STARTED = "download_started"
    DOWNLOAD_COMPLETED = "download_completed"
    PLEX_AVAILABLE = "plex_available"
    EPISODE_MARKED_AVAILABLE = "episode_marked_available"
    REQUEST_STATUS_CHANGED = "request_status_changed"
    ERROR = "error"


class ActivityLog(Base):
    __tablename__ = "activity_logs"
    __table_args__ = (
        Index("ix_activity_logs_request_id", "request_id"),
        Index("ix_activity_logs_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    request_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("requests.id"), nullable=True
    )
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    details: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now, nullable=False)

    def __repr__(self) -> str:
        return f"<ActivityLog(id={self.id}, event_type='{self.event_type}', request_id={self.request_id})>"
