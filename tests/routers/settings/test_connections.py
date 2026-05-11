"""Settings connection route and API tests."""

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.siftarr.routers import settings
from app.siftarr.services.integrations.connection_tester import ConnectionTestResult


@pytest.mark.asyncio
async def test_save_connections_persists_values_and_redirects(monkeypatch, mock_db):
    """Saving connection settings should write each field and redirect back."""

    set_runtime = AsyncMock()
    monkeypatch.setattr(settings, "_apply_runtime_setting", set_runtime)

    response = await settings.save_connections(
        MagicMock(),
        db=mock_db,
        overseerr_url="https://overseerr",
        overseerr_api_key="ov-key",
        prowlarr_url="https://prowlarr",
        prowlarr_api_key="pr-key",
        qbittorrent_url="https://qb",
        qbittorrent_api_key="qbt_test_key",
        plex_url="https://plex",
        plex_token="plex-token",
        tz="America/New_York",
    )

    # Verify each key was persisted — ignore the store instance identity
    saved_pairs = [(c.args[1], c.args[2]) for c in set_runtime.await_args_list]
    assert saved_pairs == [
        ("overseerr_url", "https://overseerr"),
        ("overseerr_api_key", "ov-key"),
        ("prowlarr_url", "https://prowlarr"),
        ("prowlarr_api_key", "pr-key"),
        ("qbittorrent_url", "https://qb"),
        ("qbittorrent_api_key", "qbt_test_key"),
        ("plex_url", "https://plex"),
        ("plex_token", "plex-token"),
        ("tz", "America/New_York"),
    ]
    mock_db.commit.assert_awaited_once()
    assert response.status_code == 303
    assert response.headers["location"] == "/settings?saved=true"


@pytest.mark.asyncio
async def test_save_connections_skips_timezone_when_not_provided(monkeypatch, mock_db):
    """Saving connections should leave timezone untouched when omitted."""

    set_runtime = AsyncMock()
    monkeypatch.setattr(settings, "_apply_runtime_setting", set_runtime)

    await settings.save_connections(MagicMock(), db=mock_db, tz=None)

    saved_keys = [saved_call.args[1] for saved_call in set_runtime.await_args_list]
    assert "tz" not in saved_keys
    mock_db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_reset_connections_redirects_back_to_settings(monkeypatch, mock_db):
    """Reset route should clear runtime overrides and preserve redirect behavior."""

    clear_runtime = AsyncMock()
    monkeypatch.setattr(settings, "_clear_runtime_settings", clear_runtime)

    response = await settings.reset_connections(MagicMock(), db=mock_db)

    clear_runtime.assert_awaited_once()
    mock_db.commit.assert_awaited_once()
    assert response.status_code == 303
    assert response.headers["location"] == "/settings?reset=true"


@pytest.mark.asyncio
async def test_apply_runtime_setting_updates_env_and_clears_cache(monkeypatch):
    """Runtime setting writes should update env and refresh the cache."""

    cache_clear = MagicMock()
    monkeypatch.setattr(settings, "reload_settings", cache_clear)
    monkeypatch.delenv("OVERSEERR_URL", raising=False)

    store = MagicMock()
    store.set = AsyncMock()

    await settings._apply_runtime_setting(store, "overseerr_url", "https://overseerr")

    store.set.assert_awaited_once_with("overseerr_url", "https://overseerr")
    assert os.environ["OVERSEERR_URL"] == "https://overseerr"
    cache_clear.assert_called_once_with()


@pytest.mark.asyncio
async def test_runtime_settings_apply_db_overrides_before_connection_tests(monkeypatch, mock_db):
    """Connection tests should use the same persisted/env merge as the settings page."""

    runtime_settings = MagicMock()
    store = AsyncMock()
    store.load_into_environ = AsyncMock()
    monkeypatch.setattr(settings, "SettingsStore", lambda db: store)
    monkeypatch.setattr(settings, "get_settings", lambda: runtime_settings)

    result = await settings._get_runtime_settings(mock_db)

    store.load_into_environ.assert_awaited_once_with()
    assert result is runtime_settings


@pytest.mark.asyncio
async def test_toggle_staging_mode_flips_runtime_setting(monkeypatch, mock_db):
    """Staging mode toggle should write the inverted runtime setting value."""

    set_runtime = AsyncMock()
    monkeypatch.setattr(settings, "_apply_runtime_setting", set_runtime)
    monkeypatch.setattr(
        settings,
        "get_settings",
        lambda: SimpleNamespace(staging_mode_enabled=True),
    )

    response = await settings.toggle_staging_mode(db=mock_db)

    # Verify key/value, ignore store instance identity
    assert set_runtime.await_args_list[0].args[1] == "staging_mode_enabled"
    assert set_runtime.await_args_list[0].args[2] == "false"
    mock_db.commit.assert_awaited_once()
    assert response.status_code == 303
    assert response.headers["location"] == "/settings"


@pytest.mark.asyncio
async def test_get_connections_api_returns_effective_connection_settings(monkeypatch, mock_db):
    """Connections API should expose the effective settings subset."""

    # Mock SettingsStore.get_effective_dict to return test data
    expected = {
        "overseerr_url": "https://overseerr",
        "overseerr_api_key": "ov-key",
        "prowlarr_url": "https://prowlarr",
        "prowlarr_api_key": "pr-key",
        "qbittorrent_url": "https://qb",
        "qbittorrent_api_key": "qbt_test_key",
        "plex_url": "https://plex",
        "plex_token": "plex-token",
        "tz": "UTC",
    }
    store = AsyncMock()
    store.get_effective_dict.return_value = expected
    monkeypatch.setattr(settings, "SettingsStore", lambda db: store)

    payload = await settings.get_connections_api(db=mock_db)

    assert payload == {
        "overseerr_url": "https://overseerr",
        "overseerr_api_key": "ov-key",
        "prowlarr_url": "https://prowlarr",
        "prowlarr_api_key": "pr-key",
        "qbittorrent_url": "https://qb",
        "qbittorrent_api_key": "qbt_test_key",
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
    get_runtime_settings = AsyncMock(return_value=effective_settings)
    monkeypatch.setattr(settings, "_get_runtime_settings", get_runtime_settings)
    tester = AsyncMock(
        return_value=ConnectionTestResult(True, f"{service_name} ok", details="detail")
    )
    monkeypatch.setattr(settings.ConnectionTester, tester_name, tester)

    response = await getattr(settings, route_name)(db=mock_db)

    get_runtime_settings.assert_awaited_once_with(mock_db)
    tester.assert_awaited_once_with(effective_settings)
    assert response.service == service_name
    assert response.success is True
    assert response.message == f"{service_name} ok"
    assert response.details == "detail"


@pytest.mark.asyncio
async def test_test_all_connections_runs_each_tester_in_order(monkeypatch, mock_db):
    """Bulk connection testing should reuse one settings object and preserve service order."""

    effective_settings = MagicMock()
    get_runtime_settings = AsyncMock(return_value=effective_settings)
    monkeypatch.setattr(settings, "_get_runtime_settings", get_runtime_settings)
    overseerr = AsyncMock(return_value=ConnectionTestResult(True, "overseerr ok", "ov"))
    prowlarr = AsyncMock(return_value=ConnectionTestResult(False, "prowlarr bad", "pr"))
    qbittorrent = AsyncMock(return_value=ConnectionTestResult(True, "qb ok", "qb"))
    plex = AsyncMock(return_value=ConnectionTestResult(True, "plex ok", None))

    monkeypatch.setattr(settings.ConnectionTester, "test_overseerr", overseerr)
    monkeypatch.setattr(settings.ConnectionTester, "test_prowlarr", prowlarr)
    monkeypatch.setattr(settings.ConnectionTester, "test_qbittorrent", qbittorrent)
    monkeypatch.setattr(settings.ConnectionTester, "test_plex", plex)

    response = await settings.test_all_connections(db=mock_db)

    get_runtime_settings.assert_awaited_once_with(mock_db)
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
