"""Rule model for filtering and scoring releases."""

import enum
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Integer, String
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.arbitratarr.models._base import Base  # noqa: PLC0414


def _utc_now() -> datetime:
    """Return current UTC time as timezone-aware datetime."""
    return datetime.now(timezone.utc)


class RuleType(str, enum.Enum):
    EXCLUSION = "exclusion"  # Reject if matches
    REQUIREMENT = "requirement"  # Must match at least one
    SCORER = "scorer"  # Add points if matches


class Rule(Base):
    __tablename__ = "rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    rule_type: Mapped[RuleType] = mapped_column(SQLEnum(RuleType), nullable=False)
    pattern: Mapped[str] = mapped_column(String(500), nullable=False)
    score: Mapped[int] = mapped_column(Integer, default=0)  # Only used for SCORER type
    priority: Mapped[int] = mapped_column(Integer, default=0)  # Lower = checked first
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now, onupdate=_utc_now)

    def __repr__(self) -> str:
        return f"<Rule(id={self.id}, name='{self.name}', type={self.rule_type})>"
