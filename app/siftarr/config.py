"""Application settings loaded from environment variables."""

import os
import time
from functools import lru_cache

from pydantic import Field, model_validator
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
    qbittorrent_api_key: str | None = None

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
    api_key: str = Field(default="dev-key-change-me", validation_alias="SIFTARR_API_KEY")
    auth_enabled: bool = False

    # Session secret key (auto-generated if not set)
    secret_key: str = Field(
        default_factory=lambda: os.urandom(32).hex(),
        description="Secret key for session signing. Auto-generated if not provided.",
    )

    cache_static_assets: bool = True

    # Search result caching (Prowlarr)
    siftarr_disable_search_cache: bool = False

    # Prowlarr TV season-sweep settings
    prowlarr_tv_page_size: int = Field(default=100, ge=1, le=500)
    prowlarr_tv_max_pages_per_query: int = Field(default=6, ge=1, le=50)
    prowlarr_tv_max_results_per_season: int = Field(default=600, ge=1, le=5000)
    prowlarr_tv_strategy_title_sxx_enabled: bool = True
    prowlarr_tv_strategy_imdb_enabled: bool = True
    prowlarr_tv_strategy_title_season_token_enabled: bool = True
    prowlarr_tv_strategy_tvdb_enabled: bool = False

    @model_validator(mode="after")
    def _validate_tv_sweep_strategies(self) -> "Settings":
        if not any(
            [
                self.prowlarr_tv_strategy_title_sxx_enabled,
                self.prowlarr_tv_strategy_imdb_enabled,
                self.prowlarr_tv_strategy_title_season_token_enabled,
                self.prowlarr_tv_strategy_tvdb_enabled,
            ]
        ):
            raise ValueError("at least one Prowlarr TV season strategy must be enabled")
        return self


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
