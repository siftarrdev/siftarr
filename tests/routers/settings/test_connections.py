"""Settings connection route and API tests."""

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call

import pytest

from app.siftarr.routers import settings
from app.siftarr.services.connection_tester import ConnectionTestResult


@pytest.mark.asyncio
async def test_save_connections_persists_values_and_redirects(monkeypatch, mock_db):
    """Saving connection settings should write each field and redirect back."""

    set_db_setting = AsyncMock()
    monkeypatch.setattr(settings, "_set_db_setting", set_db_setting)

    response = await settings.save_connections(
        MagicMock(),
        db=mock_db,
        overseerr_url="https://overseerr",
        overseerr_api_key="ov-key",
        prowlarr_url="https://prowlarr",
        prowlarr_api_key="pr-key",
        qbittorrent_url="https://qb",
        qbittorrent_username="qb-user",
        qbittorrent_password="qb-pass",
        plex_url="https://plex",
        plex_token="plex-token",
        tz="America/New_York",
    )

    assert set_db_setting.await_args_list == [
        call(mock_db, "overseerr_url", "https://overseerr", "Overseerr URL"),
        call(mock_db, "overseerr_api_key", "ov-key", "Overseerr API key"),
        call(mock_db, "prowlarr_url", "https://prowlarr", "Prowlarr URL"),
        call(mock_db, "prowlarr_api_key", "pr-key", "Prowlarr API key"),
        call(mock_db, "qbittorrent_url", "https://qb", "qBittorrent URL"),
        call(mock_db, "qbittorrent_username", "qb-user", "qBittorrent username"),
        call(mock_db, "qbittorrent_password", "qb-pass", "qBittorrent password"),
        call(mock_db, "plex_url", "https://plex", "Plex URL"),
        call(mock_db, "plex_token", "plex-token", "Plex token"),
        call(mock_db, "tz", "America/New_York", "Timezone"),
    ]
    mock_db.commit.assert_awaited_once()
    assert response.status_code == 303
    assert response.headers["location"] == "/settings?saved=true"


@pytest.mark.asyncio
async def test_save_connections_skips_timezone_when_not_provided(monkeypatch, mock_db):
    """Saving connections should leave timezone untouched when omitted."""

    set_db_setting = AsyncMock()
    monkeypatch.setattr(settings, "_set_db_setting", set_db_setting)

    await settings.save_connections(MagicMock(), db=mock_db, tz=None)

    saved_keys = [saved_call.args[1] for saved_call in set_db_setting.await_args_list]
    assert "tz" not in saved_keys
    mock_db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_reset_connections_redirects_back_to_settings():
    """Reset route should clear runtime overrides and preserve redirect behavior."""

    settings.get_settings.cache_clear()
    os.environ["OVERSEERR_URL"] = "https://overseerr"
    os.environ["PLEX_TOKEN"] = "plex-token"
    os.environ["TZ"] = "America/New_York"

    response = await settings.reset_connections(MagicMock())

    assert "OVERSEERR_URL" not in os.environ
    assert "PLEX_TOKEN" not in os.environ
    assert "TZ" not in os.environ
    assert response.status_code == 303
    assert response.headers["location"] == "/settings?reset=true"
    settings.get_settings.cache_clear()


@pytest.mark.asyncio
async def test_set_db_setting_updates_runtime_env_and_clears_cache(monkeypatch):
    """Compatibility settings writes should update runtime env-backed settings."""

    cache_clear = MagicMock()
    monkeypatch.setattr(settings.get_settings, "cache_clear", cache_clear)
    monkeypatch.delenv("OVERSEERR_URL", raising=False)

    await settings._set_db_setting(MagicMock(), "overseerr_url", "https://overseerr")

    assert os.environ["OVERSEERR_URL"] == "https://overseerr"
    cache_clear.assert_called_once_with()


@pytest.mark.asyncio
async def test_toggle_staging_mode_flips_runtime_setting(monkeypatch, mock_db):
    """Staging mode toggle should write the inverted runtime setting value."""

    set_db_setting = AsyncMock()
    monkeypatch.setattr(settings, "_set_db_setting", set_db_setting)
    monkeypatch.setattr(
        settings._jobs,
        "get_settings",
        lambda: SimpleNamespace(staging_mode_enabled=True),
    )

    response = await settings.toggle_staging_mode(db=mock_db)

    set_db_setting.assert_awaited_once_with(None, "staging_mode_enabled", "false")
    assert response.status_code == 303
    assert response.headers["location"] == "/settings"


@pytest.mark.asyncio
async def test_get_connections_api_returns_effective_connection_settings(monkeypatch, mock_db):
    """Connections API should expose the effective settings subset."""

    monkeypatch.setattr(
        settings,
        "_build_effective_settings",
        AsyncMock(
            return_value={
                "overseerr_url": "https://overseerr",
                "overseerr_api_key": "ov-key",
                "prowlarr_url": "https://prowlarr",
                "prowlarr_api_key": "pr-key",
                "qbittorrent_url": "https://qb",
                "qbittorrent_username": "qb-user",
                "qbittorrent_password": "qb-pass",
                "plex_url": "https://plex",
                "plex_token": "plex-token",
                "tz": "UTC",
            }
        ),
    )

    payload = await settings.get_connections_api(db=mock_db)

    assert payload == {
        "overseerr_url": "https://overseerr",
        "overseerr_api_key": "ov-key",
        "prowlarr_url": "https://prowlarr",
        "prowlarr_api_key": "pr-key",
        "qbittorrent_url": "https://qb",
        "qbittorrent_username": "qb-user",
        "qbittorrent_password": "qb-pass",
        "tz": "UTC",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("route_name", "tester_name", "service_name"),
    [
        ("test_overseerr_connection", "test_overseerr", "overseerr"),
        ("test_prowlarr_connection", "test_prowlarr", "prowlarr"),
        ("test_qbittorrent_connection", "test_qbittorrent", "qbittorrent"),
        ("test_plex_connection", "test_plex", "plex"),
    ],
)
async def test_individual_connection_test_routes_return_service_results(
    monkeypatch, mock_db, route_name, tester_name, service_name
):
    """Each connection test route should wrap its service result consistently."""

    effective_settings = MagicMock()
    build_effective_settings_obj = AsyncMock(return_value=effective_settings)
    tester = AsyncMock(
        return_value=ConnectionTestResult(True, f"{service_name} ok", details="detail")
    )
    monkeypatch.setattr(settings, "_build_effective_settings_obj", build_effective_settings_obj)
    monkeypatch.setattr(settings.ConnectionTester, tester_name, tester)

    response = await getattr(settings, route_name)(db=mock_db)

    build_effective_settings_obj.assert_awaited_once_with(mock_db)
    tester.assert_awaited_once_with(effective_settings)
    assert response.service == service_name
    assert response.success is True
    assert response.message == f"{service_name} ok"
    assert response.details == "detail"


@pytest.mark.asyncio
async def test_test_all_connections_runs_each_tester_in_order(monkeypatch, mock_db):
    """Bulk connection testing should reuse one settings object and preserve service order."""

    effective_settings = MagicMock()
    build_effective_settings_obj = AsyncMock(return_value=effective_settings)
    overseerr = AsyncMock(return_value=ConnectionTestResult(True, "overseerr ok", "ov"))
    prowlarr = AsyncMock(return_value=ConnectionTestResult(False, "prowlarr bad", "pr"))
    qbittorrent = AsyncMock(return_value=ConnectionTestResult(True, "qb ok", "qb"))
    plex = AsyncMock(return_value=ConnectionTestResult(True, "plex ok", None))

    monkeypatch.setattr(settings, "_build_effective_settings_obj", build_effective_settings_obj)
    monkeypatch.setattr(settings.ConnectionTester, "test_overseerr", overseerr)
    monkeypatch.setattr(settings.ConnectionTester, "test_prowlarr", prowlarr)
    monkeypatch.setattr(settings.ConnectionTester, "test_qbittorrent", qbittorrent)
    monkeypatch.setattr(settings.ConnectionTester, "test_plex", plex)

    response = await settings.test_all_connections(db=mock_db)

    build_effective_settings_obj.assert_awaited_once_with(mock_db)
    overseerr.assert_awaited_once_with(effective_settings)
    prowlarr.assert_awaited_once_with(effective_settings)
    qbittorrent.assert_awaited_once_with(effective_settings)
    plex.assert_awaited_once_with(effective_settings)
    assert [(item.service, item.success, item.message, item.details) for item in response] == [
        ("overseerr", True, "overseerr ok", "ov"),
        ("prowlarr", False, "prowlarr bad", "pr"),
        ("qbittorrent", True, "qb ok", "qb"),
        ("plex", True, "plex ok", None),
    ]
