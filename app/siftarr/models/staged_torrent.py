"""Staged torrent metadata model."""

from datetime import UTC, datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.siftarr.models._base import Base


def _utc_now() -> datetime:
    """Return current UTC time as timezone-aware datetime."""
    return datetime.now(UTC)


class StagedTorrent(Base):
    __tablename__ = "staged_torrents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    request_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # Can be null if manual

    # File info
    torrent_path: Mapped[str] = mapped_column(String(500), nullable=False)
    json_path: Mapped[str] = mapped_column(String(500), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(500), nullable=False)

    # Metadata from JSON sidecar
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    indexer: Mapped[str] = mapped_column(String(255), nullable=False)
    score: Mapped[int] = mapped_column(Integer, default=0)
    magnet_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    selection_source: Mapped[str] = mapped_column(String(20), default="rule")

    # Status
    status: Mapped[str] = mapped_column(
        String(50), default="staged"
    )  # staged, approved, discarded, replaced
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now, onupdate=_utc_now)

    # Replacement tracking (self-referential)
    replaced_by_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("staged_torrents.id"), nullable=True
    )
    replaced_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    replacement_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)

    def __repr__(self) -> str:
        return f"<StagedTorrent(id={self.id}, title='{self.title[:30]}...')>"
