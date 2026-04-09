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
