"""Release model for Prowlarr results."""

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.arbitratarr.models._base import Base  # noqa: PLC0414

if TYPE_CHECKING:
    from app.arbitratarr.models.request import Request


def _utc_now() -> datetime:
    """Return current UTC time as timezone-aware datetime."""
    return datetime.now(UTC)


class Release(Base):
    __tablename__ = "releases"

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

    # Resolution info (parsed)
    resolution: Mapped[str | None] = mapped_column(
        String(20), nullable=True
    )  # e.g., "1080p", "2160p"
    codec: Mapped[str | None] = mapped_column(String(50), nullable=True)  # e.g., "x265", "H.264"
    release_group: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # Scoring
    score: Mapped[int] = mapped_column(Integer, default=0)
    passed_rules: Mapped[bool] = mapped_column(Boolean, default=False)

    # Status tracking
    is_downloaded: Mapped[bool] = mapped_column(Boolean, default=False)
    downloaded_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now)

    # Relationships
    request: Mapped["Request"] = relationship("Request", back_populates="releases")

    def __repr__(self) -> str:
        return f"<Release(id={self.id}, title='{self.title[:50]}...', score={self.score})>"
