"""Season model for TV show tracking."""

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Index, Integer, UniqueConstraint
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.siftarr.models._base import Base
from app.siftarr.models.request import RequestStatus

if TYPE_CHECKING:
    from app.siftarr.models.episode import Episode
    from app.siftarr.models.request import Request


def _utc_now() -> datetime:
    return datetime.now(UTC)


class Season(Base):
    __tablename__ = "seasons"
    __table_args__ = (
        UniqueConstraint("request_id", "season_number", name="uq_seasons_request_season"),
        Index("ix_seasons_request_id", "request_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    request_id: Mapped[int] = mapped_column(ForeignKey("requests.id"), nullable=False)
    season_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[RequestStatus] = mapped_column(
        SQLEnum(RequestStatus), default=RequestStatus.PENDING
    )
    synced_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    request: Mapped["Request"] = relationship("Request", back_populates="seasons")
    episodes: Mapped[list["Episode"]] = relationship(
        "Episode", back_populates="season", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Season(id={self.id}, request_id={self.request_id}, season_number={self.season_number}, status={self.status})>"
