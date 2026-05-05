"""Application settings loaded from environment variables."""

import os
import time
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.siftarr.version import __version__


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
    plex_poll_interval_minutes: int = 360
    max_episode_discovery: int = 30
    plex_recent_scan_interval_minutes: int = 5
    plex_full_sync_time: str = "03:00"
    overseerr_sync_concurrency: int = 16
    plex_sync_concurrency: int = 16

    database_url: str = Field(
        default_factory=lambda: (
            f"sqlite+aiosqlite:///{os.getenv('SIFTARR_DB_PATH', '/data/db/siftarr.db')}"
        )
    )

    # Authentication settings
    api_key: str = "dev-key-change-me"
    auth_enabled: bool = False

    cache_static_assets: bool = True


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()


def reload_settings() -> None:
    """Invalidate the cached ``Settings`` singleton.

    Call after modifying ``os.environ`` so the next caller of
    :func:`get_settings` re-reads from the updated environment.
    """
    get_settings.cache_clear()


def get_static_version(settings: Settings | None = None) -> str:
    """Return a cache-busting query-string value for static assets.

    When ``cache_static_assets`` is enabled (the default) the app version is
    used so browsers only re-fetch after a release.  Disabling the setting
    (e.g. ``CACHE_STATIC_ASSETS=false`` in dev) uses a timestamp so every
    request gets fresh assets.
    """
    effective = settings or get_settings()
    if effective.cache_static_assets:
        return __version__
    return str(int(time.time()))
