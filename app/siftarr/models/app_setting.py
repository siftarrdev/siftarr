"""AppSetting model for persisting runtime-configurable settings."""

from datetime import UTC, datetime

from sqlalchemy import Column, DateTime, String, Text

from app.siftarr.models._base import Base


class AppSetting(Base):
    """Key-value store for user-configurable application settings.

    These settings are persisted across restarts and take precedence over
    environment-variable defaults when present.  The ``key`` column stores
    the Python attribute name (e.g. ``overseerr_url``) and ``value`` stores
    its string representation.
    """

    __tablename__ = "app_settings"
    __table_args__ = {"extend_existing": True}

    key = Column(String(255), primary_key=True)
    value = Column(Text, nullable=False, default="")
    updated_at = Column(DateTime, nullable=False, default=lambda: datetime.now(UTC))
