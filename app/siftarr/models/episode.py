"""Episode model for TV show tracking."""

from datetime import date
from typing import TYPE_CHECKING

from sqlalchemy import Date, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.siftarr.models._base import Base
from app.siftarr.models.request import RequestStatus

if TYPE_CHECKING:
    from app.siftarr.models.release import Release
    from app.siftarr.models.season import Season


class Episode(Base):
    __tablename__ = "episodes"
    __table_args__ = (
        UniqueConstraint("season_id", "episode_number", name="uq_episodes_season_episode"),
        Index("ix_episodes_season_id", "season_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    season_id: Mapped[int] = mapped_column(ForeignKey("seasons.id"), nullable=False)
    episode_number: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str | None] = mapped_column(String(500), nullable=True)
    air_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[RequestStatus] = mapped_column(
        SQLEnum(RequestStatus), default=RequestStatus.RECEIVED
    )
    release_id: Mapped[int | None] = mapped_column(ForeignKey("releases.id"), nullable=True)

    season: Mapped["Season"] = relationship("Season", back_populates="episodes")
    release: Mapped["Release | None"] = relationship("Release")

    def __repr__(self) -> str:
        return f"<Episode(id={self.id}, season_id={self.season_id}, episode_number={self.episode_number}, status={self.status})>"
