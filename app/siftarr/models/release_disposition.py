"""Durable, request-scoped operator decisions about release identities."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.siftarr.models._base import Base, utc_now


class ReleaseDisposition(Base):
    __tablename__ = "release_dispositions"
    __table_args__ = (
        UniqueConstraint(
            "request_id", "release_key", "target_scope", name="uq_release_disposition_identity"
        ),
        Index("ix_release_dispositions_request_id", "request_id"),
        Index("ix_release_dispositions_expires_at", "expires_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    request_id: Mapped[int] = mapped_column(
        ForeignKey("requests.id", ondelete="CASCADE"), nullable=False
    )
    release_key: Mapped[str] = mapped_column(String(128), nullable=False)
    info_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    target_scope: Mapped[str] = mapped_column(String(255), nullable=False, default="request")
    kind: Mapped[str] = mapped_column(String(32), nullable=False, default="rejected")
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    staged_torrent_id: Mapped[int | None] = mapped_column(
        ForeignKey("staged_torrents.id"), nullable=True
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
