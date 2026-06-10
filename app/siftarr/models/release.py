"""Release model for Prowlarr results."""

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import JSON, BigInteger, Boolean, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.siftarr.models._base import Base, utc_now  # noqa: PLC0414

if TYPE_CHECKING:
    from app.siftarr.models.request import Request


class Release(Base):
    __tablename__ = "releases"
    __table_args__ = (
        Index("ix_releases_request_id", "request_id"),
        Index("ix_releases_score", "score"),
        Index(
            "ix_releases_request_season_episode", "request_id", "season_number", "episode_number"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    request_id: Mapped[int] = mapped_column(ForeignKey("requests.id"), nullable=False)

    # Release info
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    size: Mapped[int] = mapped_column(BigInteger, nullable=False)  # bytes
    seeders: Mapped[int] = mapped_column(Integer, default=0)
    leechers: Mapped[int] = mapped_column(Integer, default=0)
    download_url: Mapped[str] = mapped_column(Text, nullable=False)
    magnet_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    info_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    indexer: Mapped[str] = mapped_column(String(255), nullable=False)
    publish_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    files: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Resolution info (parsed)
    resolution: Mapped[str | None] = mapped_column(
        String(20), nullable=True
    )  # e.g., "1080p", "2160p"
    codec: Mapped[str | None] = mapped_column(String(50), nullable=True)  # e.g., "x265", "H.264"
    release_group: Mapped[str | None] = mapped_column(String(100), nullable=True)
    uploaded_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    season_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    episode_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    season_coverage: Mapped[str | None] = mapped_column(String(100), nullable=True)
    search_source: Mapped[str | None] = mapped_column(
        String(20), nullable=True, default="automatic"
    )

    # Scoring
    score: Mapped[int] = mapped_column(Integer, default=0)
    passed_rules: Mapped[bool] = mapped_column(Boolean, default=False)
    rejection_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    rule_evidence: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    parse_metadata: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)

    # Relationships
    request: Mapped[Request] = relationship("Request", back_populates="releases")

    def __repr__(self) -> str:
        return f"<Release(id={self.id}, title='{self.title[:50]}...', score={self.score})>"
