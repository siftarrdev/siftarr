"""Application settings loaded from environment variables."""

import os
from functools import lru_cache

from pydantic import Field
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
    puid: int = 1000
    pgid: int = 1000

    # Overseerr settings
    overseerr_url: str | None = None
    overseerr_api_key: str | None = None

    # Prowlarr settings
    prowlarr_url: str | None = None
    prowlarr_api_key: str | None = None

    # Plex settings
    plex_url: str | None = None
    plex_token: str | None = None

    # qBittorrent settings
    qbittorrent_url: str | None = None
    qbittorrent_username: str = "admin"
    qbittorrent_password: str = "adminadmin"

    # Application settings (with defaults)
    staging_mode_enabled: bool = True
    retry_interval_hours: int = 24
    max_retry_duration_days: int = 7
    episode_sync_stale_hours: int = 24
    max_episode_discovery: int = 30

    database_url: str = Field(
        default_factory=lambda: (
            f"sqlite+aiosqlite:///{os.getenv('SIFTARR_DB_PATH', '/data/db/siftarr.db')}"
        )
    )


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
