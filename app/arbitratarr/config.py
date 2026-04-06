"""Application settings loaded from environment variables."""

import os
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # System settings
    tz: str = "UTC"
    puid: int = 568
    pgid: int = 568

    # Overseerr settings
    overseerr_url: str | None = None
    overseerr_api_key: str | None = None

    # Prowlarr settings
    prowlarr_url: str | None = None
    prowlarr_api_key: str | None = None

    # qBittorrent settings
    qbittorrent_url: str | None = None
    qbittorrent_username: str = "admin"
    qbittorrent_password: str = "adminadmin"

    # Application settings (with defaults)
    staging_mode_enabled: bool = False
    retry_interval_hours: int = 24
    max_retry_duration_days: int = 7

    # Database path (relative to project root)
    _database_path: Path = Path(__file__).parent.parent.parent / "data" / "db" / "arbitratarr.db"
    database_url: str = f"sqlite+aiosqlite:///{_database_path}"


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
