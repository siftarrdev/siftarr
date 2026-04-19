"""Persisted state for Plex background scan jobs."""

from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.siftarr.models._base import Base  # noqa: PLC0414


def _utc_now() -> datetime:
    """Return current UTC time as timezone-aware datetime."""
    return datetime.now(UTC)


class PlexScanState(Base):
    """Tracks lock and checkpoint state for a Plex scan job."""

    __tablename__ = "plex_scan_state"

    job_name: Mapped[str] = mapped_column(String(100), primary_key=True)
    lock_owner: Mapped[str | None] = mapped_column(String(255), nullable=True)
    lock_acquired_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    lock_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    checkpoint_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    metrics_payload: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now, onupdate=_utc_now)

    def __repr__(self) -> str:
        return f"<PlexScanState(job_name='{self.job_name}', lock_owner='{self.lock_owner}')>"
