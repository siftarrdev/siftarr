"""Helpers for resolving effective runtime settings."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.siftarr.config import Settings, get_settings
from app.siftarr.models.settings import Settings as DBSettings

SETTING_KEYS = {
    "overseerr_url",
    "overseerr_api_key",
    "prowlarr_url",
    "prowlarr_api_key",
    "plex_url",
    "plex_token",
    "qbittorrent_url",
    "qbittorrent_username",
    "qbittorrent_password",
    "staging_mode_enabled",
    "tz",
}


async def get_effective_settings(db: AsyncSession | None = None) -> Settings:
    """Return settings with database overrides applied when available."""
    env_settings = get_settings()

    if db is None:
        return env_settings

    result = await db.execute(select(DBSettings).where(DBSettings.key.in_(SETTING_KEYS)))
    db_settings = {setting.key: setting.value for setting in result.scalars().all()}

    def get_value(key: str, fallback: str | None) -> str | None:
        value = db_settings.get(key)
        if value is None or value == "":
            return fallback
        return value

    def get_optional_env_value(key: str) -> str | None:
        value = getattr(env_settings, key, None)
        return value if isinstance(value, str) or value is None else None

    def get_bool_value(key: str, fallback: bool) -> bool:
        value = db_settings.get(key)
        if value is None or value == "":
            return fallback
        return str(value).strip().lower() in {"1", "true", "yes", "on"}

    return Settings(
        overseerr_url=get_value("overseerr_url", get_optional_env_value("overseerr_url")),
        overseerr_api_key=get_value(
            "overseerr_api_key", get_optional_env_value("overseerr_api_key")
        ),
        prowlarr_url=get_value("prowlarr_url", get_optional_env_value("prowlarr_url")),
        prowlarr_api_key=get_value("prowlarr_api_key", get_optional_env_value("prowlarr_api_key")),
        plex_url=get_value("plex_url", get_optional_env_value("plex_url")),
        plex_token=get_value("plex_token", get_optional_env_value("plex_token")),
        qbittorrent_url=get_value("qbittorrent_url", get_optional_env_value("qbittorrent_url")),
        qbittorrent_username=get_value("qbittorrent_username", env_settings.qbittorrent_username)
        or env_settings.qbittorrent_username,
        qbittorrent_password=get_value("qbittorrent_password", env_settings.qbittorrent_password)
        or env_settings.qbittorrent_password,
        tz=get_value("tz", env_settings.tz) or env_settings.tz,
        database_url=env_settings.database_url,
        staging_mode_enabled=get_bool_value(
            "staging_mode_enabled", env_settings.staging_mode_enabled
        ),
        retry_interval_hours=env_settings.retry_interval_hours,
        max_retry_duration_days=env_settings.max_retry_duration_days,
        overseerr_sync_concurrency=env_settings.overseerr_sync_concurrency,
        plex_sync_concurrency=env_settings.plex_sync_concurrency,
        puid=env_settings.puid,
        pgid=env_settings.pgid,
    )


async def get_db_setting(db: AsyncSession, key: str) -> str | None:
    """Get a single setting value from the database."""
    result = await db.execute(select(DBSettings).where(DBSettings.key == key))
    setting = result.scalar_one_or_none()
    return setting.value if setting else None
