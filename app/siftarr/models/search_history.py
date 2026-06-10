"""Search run history models."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.siftarr.models._base import Base, utc_now


class SearchRun(Base):
    __tablename__ = "search_runs"
    __table_args__ = (
        Index("ix_search_runs_request_id", "request_id"),
        Index("ix_search_runs_created_at", "created_at"),
        Index("ix_search_runs_status", "status"),
        Index("ix_search_runs_outcome", "outcome"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    request_id: Mapped[int] = mapped_column(ForeignKey("requests.id"), nullable=False)
    trigger: Mapped[str | None] = mapped_column(String(50), nullable=True)
    source: Mapped[str | None] = mapped_column(String(50), nullable=True)
    search_mode: Mapped[str | None] = mapped_column(String(50), nullable=True)
    scope: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="running", nullable=False)
    outcome: Mapped[str | None] = mapped_column(String(50), nullable=True)
    counts: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    winner_summary: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    failure_summaries: Mapped[list[dict[str, object]] | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)

    candidates: Mapped[list[SearchRunCandidate]] = relationship(
        "SearchRunCandidate", cascade="all, delete-orphan", back_populates="search_run"
    )


class SearchRunCandidate(Base):
    __tablename__ = "search_run_candidates"
    __table_args__ = (
        Index("ix_search_run_candidates_search_run_id", "search_run_id"),
        Index("ix_search_run_candidates_request_id", "request_id"),
        Index("ix_search_run_candidates_status", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    search_run_id: Mapped[int] = mapped_column(ForeignKey("search_runs.id"), nullable=False)
    request_id: Mapped[int] = mapped_column(ForeignKey("requests.id"), nullable=False)
    stored_release_id: Mapped[int | None] = mapped_column(ForeignKey("releases.id"), nullable=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    score: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    summary: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    rule_evidence: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    parse_metadata: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)

    search_run: Mapped[SearchRun] = relationship("SearchRun", back_populates="candidates")
