"""Immutable stats metric tables."""

from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.siftarr.models._base import Base, utc_now


class StatsReleaseFact(Base):
    """Selected/approved release facts used by stats aggregation."""

    __tablename__ = "stats_release_facts"
    __table_args__ = (
        Index("ix_stats_release_facts_request_id", "request_id"),
        Index("ix_stats_release_facts_approved_at", "approved_at"),
        Index("ix_stats_release_facts_indexer", "indexer"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    request_id: Mapped[int] = mapped_column(ForeignKey("requests.id"), nullable=False)
    release_id: Mapped[int | None] = mapped_column(ForeignKey("releases.id"), nullable=True)
    staged_torrent_id: Mapped[int | None] = mapped_column(
        ForeignKey("staged_torrents.id"), nullable=True
    )
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    indexer: Mapped[str | None] = mapped_column(String(255), nullable=True)
    resolution: Mapped[str | None] = mapped_column(String(20), nullable=True)
    resolution_bucket: Mapped[str | None] = mapped_column(String(20), nullable=True)
    selection_source: Mapped[str] = mapped_column(String(20), nullable=False)
    approved_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)


class StatsRuleOutcome(Base):
    """Per-rule outcome for a searched release evaluation."""

    __tablename__ = "stats_rule_outcomes"
    __table_args__ = (
        Index("ix_stats_rule_outcomes_request_id", "request_id"),
        Index("ix_stats_rule_outcomes_created_at", "created_at"),
        Index("ix_stats_rule_outcomes_rule_id", "rule_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    request_id: Mapped[int] = mapped_column(ForeignKey("requests.id"), nullable=False)
    release_id: Mapped[int | None] = mapped_column(ForeignKey("releases.id"), nullable=True)
    release_title: Mapped[str] = mapped_column(String(500), nullable=False)
    rule_id: Mapped[int | None] = mapped_column(ForeignKey("rules.id"), nullable=True)
    rule_name: Mapped[str] = mapped_column(String(255), nullable=False)
    matched: Mapped[int] = mapped_column(Integer, nullable=False)
    outcome: Mapped[str] = mapped_column(String(30), nullable=False)
    score_delta: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    rejection_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)


class StatsTimingEvent(Base):
    """Durable timing events/spans for search and approval processing."""

    __tablename__ = "stats_timing_events"
    __table_args__ = (
        Index("ix_stats_timing_events_request_id", "request_id"),
        Index("ix_stats_timing_events_event_name", "event_name"),
        Index("ix_stats_timing_events_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    request_id: Mapped[int | None] = mapped_column(ForeignKey("requests.id"), nullable=True)
    activity_log_id: Mapped[int | None] = mapped_column(
        ForeignKey("activity_logs.id"), nullable=True
    )
    event_name: Mapped[str] = mapped_column(String(50), nullable=False)
    correlation_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    duration_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    details: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)
