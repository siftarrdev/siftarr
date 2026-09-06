"""Durable observations for approved qBittorrent download attempts."""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.siftarr.models._base import Base, utc_now

HEALTH_STATUS_MONITORING = "monitoring"
HEALTH_STATUS_SUSPENDED = "suspended"
HEALTH_STATUS_WARNING = "warning"
HEALTH_STATUS_RECOVERY_ELIGIBLE = "recovery_eligible"
HEALTH_STATUS_MISSING = "missing"
HEALTH_STATUS_AMBIGUOUS = "ambiguous"
HEALTH_STATUS_HEALTHY = "healthy"
HEALTH_STATUS_RECOVERED = "recovered"

HEALTH_PROBLEM_STATUSES = (
    HEALTH_STATUS_WARNING,
    HEALTH_STATUS_RECOVERY_ELIGIBLE,
    HEALTH_STATUS_MISSING,
    HEALTH_STATUS_AMBIGUOUS,
)


class DownloadHealth(Base):
    """The current health state for one approved staged torrent.

    A row is deliberately keyed to ``staged_torrent_id`` rather than to a
    qBittorrent poll.  This keeps the table small and makes a recovery action
    idempotent while retaining the original staged torrent as an operator
    reference.  qBittorrent is never mutated by this model or its service.
    """

    __tablename__ = "download_health"
    __table_args__ = (
        Index("ix_download_health_request_id", "request_id"),
        Index("ix_download_health_status", "status"),
        Index("ix_download_health_last_observed_at", "last_observed_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    staged_torrent_id: Mapped[int] = mapped_column(
        ForeignKey("staged_torrents.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    request_id: Mapped[int | None] = mapped_column(
        ForeignKey("requests.id", ondelete="CASCADE"), nullable=True
    )
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    info_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    coverage_scope: Mapped[str | None] = mapped_column(String(255), nullable=True)

    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=HEALTH_STATUS_MONITORING
    )
    first_observed_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)
    last_observed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    missing_since: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    last_progress: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_progress_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    progress_started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    suspended_since: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    suspended_seconds: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    last_state: Mapped[str | None] = mapped_column(String(64), nullable=True)
    qbit_added_on: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    qbit_last_activity: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    qbit_completed_on: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    warning_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    recovery_eligible_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    recovered_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    recovery_disposition: Mapped[str | None] = mapped_column(String(32), nullable=True)
    cooldown_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_observation: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)


# The alias keeps the domain vocabulary useful to callers that describe each
# row as an attempt, without creating a second SQLAlchemy mapping.
DownloadAttempt = DownloadHealth
