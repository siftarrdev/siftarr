"""Request model for Overseerr requests."""

import enum
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Index, Integer, String
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.siftarr.models._base import Base  # noqa: PLC0414

if TYPE_CHECKING:
    from app.siftarr.models.release import Release
    from app.siftarr.models.season import Season


def _utc_now() -> datetime:
    """Return current UTC time as timezone-aware datetime."""
    return datetime.now(UTC)


class MediaType(enum.StrEnum):
    MOVIE = "movie"
    TV = "tv"


class RequestStatus(enum.StrEnum):
    SEARCHING = "searching"
    PENDING = "pending"
    UNRELEASED = "unreleased"
    STAGED = "staged"
    DOWNLOADING = "downloading"
    COMPLETED = "completed"
    FAILED = "failed"
    DENIED = "denied"


ACTIVE_STAGING_WORKFLOW_STATUSES: tuple[RequestStatus, ...] = (
    RequestStatus.STAGED,
    RequestStatus.DOWNLOADING,
)


def is_active_staging_workflow_status(status: RequestStatus | str | None) -> bool:
    """Return whether a request state is still in active staging/downloading."""
    if isinstance(status, RequestStatus):
        return status in ACTIVE_STAGING_WORKFLOW_STATUSES
    if status is None:
        return False
    try:
        return RequestStatus(status) in ACTIVE_STAGING_WORKFLOW_STATUSES
    except ValueError:
        return False


class Request(Base):
    __tablename__ = "requests"
    __table_args__ = (
        Index("ix_requests_status", "status"),
        Index("ix_requests_media_type", "media_type"),
        Index("ix_requests_created_at", "created_at"),
        Index("ix_requests_next_retry_at", "next_retry_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    external_id: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    media_type: Mapped[MediaType] = mapped_column(SQLEnum(MediaType), nullable=False)
    tmdb_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tvdb_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[RequestStatus] = mapped_column(
        SQLEnum(RequestStatus), default=RequestStatus.PENDING
    )
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, default=None)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    last_plex_check_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, default=None
    )
    requester_username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    requester_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now, onupdate=_utc_now)
    overseerr_request_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    plex_rating_key: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # Relationships
    releases: Mapped[list["Release"]] = relationship(
        "Release", back_populates="request", cascade="all, delete-orphan"
    )
    seasons: Mapped[list["Season"]] = relationship(
        "Season", back_populates="request", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Request(id={self.id}, title='{self.title}', status={self.status})>"
