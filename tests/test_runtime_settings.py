"""Tests for runtime settings resolution."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.siftarr.services import runtime_settings


@pytest.mark.asyncio
async def test_get_effective_settings_uses_db_staging_flag(monkeypatch):
    """Database staging flag should override the environment default."""
    env_settings = MagicMock(
        overseerr_url=None,
        overseerr_api_key=None,
        prowlarr_url=None,
        prowlarr_api_key=None,
        qbittorrent_url=None,
        qbittorrent_username="admin",
        qbittorrent_password="adminadmin",
        tz="UTC",
        database_url="sqlite+aiosqlite:////tmp/test.db",
        staging_mode_enabled=True,
        retry_interval_hours=24,
        max_retry_duration_days=7,
        episode_sync_stale_hours=24,
        max_episode_discovery=30,
        plex_poll_interval_minutes=15,
        plex_recent_scan_interval_minutes=5,
        plex_full_reconcile_interval_minutes=360,
        plex_checkpoint_buffer_minutes=10,
        overseerr_sync_concurrency=16,
        plex_sync_concurrency=16,
        puid=1000,
        pgid=1000,
    )
    monkeypatch.setattr(runtime_settings, "get_settings", lambda: env_settings)

    db = AsyncMock()
    db_result = MagicMock()
    db_result.scalars.return_value.all.return_value = [
        MagicMock(key="staging_mode_enabled", value="false")
    ]
    db.execute.return_value = db_result

    settings = await runtime_settings.get_effective_settings(db)

    assert settings.staging_mode_enabled is False


@pytest.mark.asyncio
async def test_get_effective_settings_includes_plex_overrides(monkeypatch):
    """Database Plex settings should flow into effective runtime settings."""
    env_settings = MagicMock(
        overseerr_url=None,
        overseerr_api_key=None,
        prowlarr_url=None,
        prowlarr_api_key=None,
        plex_url="http://env-plex:32400",
        plex_token="env-token",
        qbittorrent_url=None,
        qbittorrent_username="admin",
        qbittorrent_password="adminadmin",
        tz="UTC",
        database_url="sqlite+aiosqlite:////tmp/test.db",
        staging_mode_enabled=True,
        retry_interval_hours=24,
        max_retry_duration_days=7,
        episode_sync_stale_hours=24,
        max_episode_discovery=30,
        plex_poll_interval_minutes=15,
        plex_recent_scan_interval_minutes=5,
        plex_full_reconcile_interval_minutes=360,
        plex_checkpoint_buffer_minutes=10,
        overseerr_sync_concurrency=16,
        plex_sync_concurrency=16,
        puid=1000,
        pgid=1000,
    )
    monkeypatch.setattr(runtime_settings, "get_settings", lambda: env_settings)

    db = AsyncMock()
    db_result = MagicMock()
    db_result.scalars.return_value.all.return_value = [
        MagicMock(key="plex_url", value="http://db-plex:32400"),
        MagicMock(key="plex_token", value="db-token"),
    ]
    db.execute.return_value = db_result

    settings = await runtime_settings.get_effective_settings(db)

    assert settings.plex_url == "http://db-plex:32400"
    assert settings.plex_token == "db-token"


@pytest.mark.asyncio
async def test_get_effective_settings_preserves_env_concurrency_caps(monkeypatch):
    """Env concurrency caps should survive DB-backed runtime settings resolution."""
    env_settings = MagicMock(
        overseerr_url=None,
        overseerr_api_key=None,
        prowlarr_url=None,
        prowlarr_api_key=None,
        plex_url=None,
        plex_token=None,
        qbittorrent_url=None,
        qbittorrent_username="admin",
        qbittorrent_password="adminadmin",
        tz="UTC",
        database_url="sqlite+aiosqlite:////tmp/test.db",
        staging_mode_enabled=True,
        retry_interval_hours=24,
        max_retry_duration_days=7,
        episode_sync_stale_hours=24,
        max_episode_discovery=30,
        plex_poll_interval_minutes=15,
        plex_recent_scan_interval_minutes=5,
        plex_full_reconcile_interval_minutes=360,
        plex_checkpoint_buffer_minutes=10,
        overseerr_sync_concurrency=5,
        plex_sync_concurrency=9,
        puid=1000,
        pgid=1000,
    )
    monkeypatch.setattr(runtime_settings, "get_settings", lambda: env_settings)

    db = AsyncMock()
    db_result = MagicMock()
    db_result.scalars.return_value.all.return_value = []
    db.execute.return_value = db_result

    settings = await runtime_settings.get_effective_settings(db)

    assert settings.overseerr_sync_concurrency == 5
    assert settings.plex_sync_concurrency == 9


@pytest.mark.asyncio
async def test_get_effective_settings_includes_split_plex_scan_overrides(monkeypatch):
    """Database scan cadence settings should override env defaults."""
    env_settings = MagicMock(
        overseerr_url=None,
        overseerr_api_key=None,
        prowlarr_url=None,
        prowlarr_api_key=None,
        plex_url=None,
        plex_token=None,
        qbittorrent_url=None,
        qbittorrent_username="admin",
        qbittorrent_password="adminadmin",
        tz="UTC",
        database_url="sqlite+aiosqlite:////tmp/test.db",
        staging_mode_enabled=True,
        retry_interval_hours=24,
        max_retry_duration_days=7,
        episode_sync_stale_hours=24,
        max_episode_discovery=30,
        plex_poll_interval_minutes=15,
        plex_recent_scan_interval_minutes=5,
        plex_full_reconcile_interval_minutes=360,
        plex_checkpoint_buffer_minutes=10,
        overseerr_sync_concurrency=16,
        plex_sync_concurrency=16,
        puid=1000,
        pgid=1000,
    )
    monkeypatch.setattr(runtime_settings, "get_settings", lambda: env_settings)

    db = AsyncMock()
    db_result = MagicMock()
    db_result.scalars.return_value.all.return_value = [
        MagicMock(key="plex_recent_scan_interval_minutes", value="7"),
        MagicMock(key="plex_full_reconcile_interval_minutes", value="720"),
        MagicMock(key="plex_checkpoint_buffer_minutes", value="12"),
    ]
    db.execute.return_value = db_result

    settings = await runtime_settings.get_effective_settings(db)

    assert settings.plex_recent_scan_interval_minutes == 7
    assert settings.plex_full_reconcile_interval_minutes == 720
    assert settings.plex_checkpoint_buffer_minutes == 12
