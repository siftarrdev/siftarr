"""Request model for Overseerr requests."""

import enum
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Integer, String
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.arbitratarr.models._base import Base  # noqa: PLC0414

if TYPE_CHECKING:
    from app.arbitratarr.models.pending_queue import PendingQueue
    from app.arbitratarr.models.release import Release


def _utc_now() -> datetime:
    """Return current UTC time as timezone-aware datetime."""
    return datetime.now(UTC)


class MediaType(enum.StrEnum):
    MOVIE = "movie"
    TV = "tv"


class RequestStatus(enum.StrEnum):
    RECEIVED = "received"
    SEARCHING = "searching"
    PENDING = "pending"
    STAGED = "staged"
    DOWNLOADING = "downloading"
    COMPLETED = "completed"
    FAILED = "failed"


class Request(Base):
    __tablename__ = "requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    external_id: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    media_type: Mapped[MediaType] = mapped_column(SQLEnum(MediaType), nullable=False)
    tmdb_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tvdb_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    requested_seasons: Mapped[str | None] = mapped_column(
        String(100), nullable=True
    )  # JSON array string
    requested_episodes: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )  # JSON array string
    status: Mapped[RequestStatus] = mapped_column(
        SQLEnum(RequestStatus), default=RequestStatus.RECEIVED
    )
    requester_username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    requester_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now, onupdate=_utc_now)
    overseerr_request_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Relationships
    releases: Mapped[list["Release"]] = relationship(
        "Release", back_populates="request", cascade="all, delete-orphan"
    )
    pending_item: Mapped["PendingQueue | None"] = relationship(
        "PendingQueue", back_populates="request", uselist=False
    )

    def __repr__(self) -> str:
        return f"<Request(id={self.id}, title='{self.title}', status={self.status})>"
