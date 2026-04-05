"""Staged torrent metadata model."""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from arbitratarr.models import Base


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

    # Status
    status: Mapped[str] = mapped_column(String(50), default="staged")  # staged, approved, discarded
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    def __repr__(self) -> str:
        return f"<StagedTorrent(id={self.id}, title='{self.title[:30]}...')>"
