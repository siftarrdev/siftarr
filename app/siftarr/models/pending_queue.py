"""Pending queue model for items awaiting retry."""

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.siftarr.models._base import Base  # noqa: PLC0414

if TYPE_CHECKING:
    from app.siftarr.models.request import Request


def _utc_now() -> datetime:
    """Return current UTC time as timezone-aware datetime."""
    return datetime.now(UTC)


class PendingQueue(Base):
    __tablename__ = "pending_queue"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    request_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("requests.id"), unique=True, nullable=False
    )
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    next_retry_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    last_error: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now, onupdate=_utc_now)

    # Relationships
    request: Mapped["Request"] = relationship("Request", back_populates="pending_item")

    def __repr__(self) -> str:
        return f"<PendingQueue(request_id={self.request_id}, retry_count={self.retry_count})>"
