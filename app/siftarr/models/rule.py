"""Rule model for filtering and scoring releases."""

import enum
from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, Float, Integer, String
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.siftarr.models._base import Base  # noqa: PLC0414


def _utc_now() -> datetime:
    """Return current UTC time as timezone-aware datetime."""
    return datetime.now(UTC)


class RuleType(enum.StrEnum):
    EXCLUSION = "exclusion"  # Reject if matches
    REQUIREMENT = "requirement"  # Must match at least one
    SCORER = "scorer"  # Add points if matches
    SIZE_LIMIT = "size_limit"  # Reject if outside configured size range


class Rule(Base):
    __tablename__ = "rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    rule_type: Mapped[RuleType] = mapped_column(SQLEnum(RuleType), nullable=False)
    media_scope: Mapped[str] = mapped_column(String(20), default="both")
    pattern: Mapped[str] = mapped_column(String(500), nullable=False)
    score: Mapped[int] = mapped_column(Integer, default=0)  # Only used for SCORER type
    min_size_gb: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_size_gb: Mapped[float | None] = mapped_column(Float, nullable=True)
    priority: Mapped[int] = mapped_column(Integer, default=0)  # Lower = checked first
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now, onupdate=_utc_now)

    def __repr__(self) -> str:
        return f"<Rule(id={self.id}, name='{self.name}', type={self.rule_type})>"
