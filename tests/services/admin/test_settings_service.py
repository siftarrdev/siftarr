import asyncio
import os
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest
from sqlalchemy import select

from app.siftarr.config import PLACEHOLDER_API_KEY, get_settings, reload_settings
from app.siftarr.models.app_setting import AppSetting
from app.siftarr.services.admin.settings_service import (
    PLEX_LAST_SYNC_SUCCESS_KEY,
    RESTORABLE_SETTING_KEYS,
    SECRET_SETTING_KEYS,
    SettingsBackupError,
    SettingsStore,
    is_sync_timestamp_stale,
    parse_sync_timestamp,
    prepare_overseerr_import,
    serialize_sync_timestamp,
)
from app.siftarr.services.auth_service import require_auth


@pytest.fixture(autouse=True)
def _clean_api_key_env(monkeypatch):
    monkeypatch.delenv("SIFTARR_API_KEY", raising=False)
    monkeypatch.delenv("PLEX_CLAIMED_ID", raising=False)
    monkeypatch.delenv("OVERSEERR_URL", raising=False)
    monkeypatch.delenv("OVERSEERR_API_KEY", raising=False)
    monkeypatch.delenv("PROWLARR_URL", raising=False)
    monkeypatch.delenv("OVERSEERR_POLL_INTERVAL_MINUTES", raising=False)
    monkeypatch.delenv("QBITTORRENT_COMPLETION_POLL_INTERVAL_SECONDS", raising=False)
    monkeypatch.delenv("PLEX_FAST_SYNC_INTERVAL_MINUTES", raising=False)
    monkeypatch.delenv("PLEX_FULL_SYNC_FREQUENCY", raising=False)
    monkeypatch.delenv("PLEX_FULL_SYNC_TIME", raising=False)
    monkeypatch.delenv("QBITTORRENT_MOVE_ENABLED", raising=False)
    monkeypatch.delenv("QBITTORRENT_MOVE_COMPLETED_DIR", raising=False)
    monkeypatch.delenv("QBITTORRENT_MOVE_MOVIE_ROOT", raising=False)
    monkeypatch.delenv("QBITTORRENT_MOVE_TV_ROOT", raising=False)
    monkeypatch.delenv("QBITTORRENT_MOVE_UNMANAGED_FALLBACK_ENABLED", raising=False)
    monkeypatch.delenv("QBITTORRENT_MOVE_RETENTION_WEEKS", raising=False)
    reload_settings()
    yield
    for key in (
        "SIFTARR_API_KEY",
        "PLEX_CLAIMED_ID",
        "PLEX_USERNAME",
        "PLEX_THUMB",
        "PLEX_TOKEN",
        "OVERSEERR_URL",
        "OVERSEERR_API_KEY",
        "PROWLARR_URL",
        "OVERSEERR_POLL_INTERVAL_MINUTES",
        "QBITTORRENT_COMPLETION_POLL_INTERVAL_SECONDS",
        "PLEX_FAST_SYNC_INTERVAL_MINUTES",
        "PLEX_FULL_SYNC_FREQUENCY",
        "PLEX_FULL_SYNC_TIME",
        "QBITTORRENT_MOVE_ENABLED",
        "QBITTORRENT_MOVE_COMPLETED_DIR",
        "QBITTORRENT_MOVE_MOVIE_ROOT",
        "QBITTORRENT_MOVE_TV_ROOT",
        "QBITTORRENT_MOVE_UNMANAGED_FALLBACK_ENABLED",
        "QBITTORRENT_MOVE_RETENTION_WEEKS",
    ):
        os.environ.pop(key, None)
    reload_settings()


@pytest.mark.parametrize(
    ("stored", "env_name", "expected"),
    [
        pytest.param(
            {
                "overseerr_poll_interval_minutes": "15",
                "qbittorrent_completion_poll_interval_seconds": "45",
                "plex_fast_sync_interval_minutes": "7",
                "plex_full_sync_frequency": "weekly",
                "plex_full_sync_time": "04:30",
            },
            "OVERSEERR_POLL_INTERVAL_MINUTES",
            {
                "overseerr_poll_interval_minutes": 15,
                "qbittorrent_completion_poll_interval_seconds": 45,
                "plex_fast_sync_interval_minutes": 7,
                "plex_full_sync_frequency": "weekly",
                "plex_full_sync_time": "04:30",
            },
            id="scheduler",
        ),
        pytest.param(
            {
                "qbittorrent_move_enabled": "true",
                "qbittorrent_move_completed_dir": "/downloads/complete",
                "qbittorrent_move_movie_root": "/media/Movies",
                "qbittorrent_move_tv_root": "/media/TV",
                "qbittorrent_move_unmanaged_fallback_enabled": "true",
                "qbittorrent_move_retention_weeks": "8",
            },
            "QBITTORRENT_MOVE_ENABLED",
            {
                "qbittorrent_move_enabled": True,
                "qbittorrent_move_completed_dir": "/downloads/complete",
                "qbittorrent_move_movie_root": "/media/Movies",
                "qbittorrent_move_tv_root": "/media/TV",
                "qbittorrent_move_unmanaged_fallback_enabled": True,
                "qbittorrent_move_retention_weeks": 8,
            },
            id="qbittorrent_move",
        ),
    ],
)
async def test_stored_settings_load_into_environ_runtime_and_effective_dict(
    session_maker, stored, env_name, expected
):
    async with session_maker() as session, session.begin():
        store = SettingsStore(session)
        for key, value in stored.items():
            await store.set(key, value)
        await store.load_into_environ()
        effective = await store.get_effective_dict()

    assert env_name in os.environ
    runtime = get_settings()
    for key, value in expected.items():
        assert getattr(runtime, key) == value
        assert effective[key] == value


@pytest.mark.asyncio
async def test_ensure_runtime_api_key_generates_and_persists_when_missing(session_maker):
    async with session_maker() as session:
        async with session.begin():
            generated = await SettingsStore(session).ensure_runtime_api_key()

        persisted = await session.scalar(select(AppSetting).where(AppSetting.key == "api_key"))

    assert generated
    assert generated != PLACEHOLDER_API_KEY
    assert persisted is not None
    assert persisted.value == generated
    assert os.environ["SIFTARR_API_KEY"] == generated
    assert get_settings().api_key == generated


@pytest.mark.asyncio
async def test_ensure_runtime_api_key_replaces_persisted_placeholder(session_maker):
    async with session_maker() as session:
        async with session.begin():
            await SettingsStore(session).set("api_key", PLACEHOLDER_API_KEY)

        async with session.begin():
            generated = await SettingsStore(session).ensure_runtime_api_key()

        persisted = await session.scalar(select(AppSetting).where(AppSetting.key == "api_key"))

    assert generated != PLACEHOLDER_API_KEY
    assert persisted is not None
    assert persisted.value == generated


@pytest.mark.asyncio
async def test_ensure_runtime_api_key_preserves_explicit_env_override(
    monkeypatch,
    session_maker,
):
    monkeypatch.setenv("SIFTARR_API_KEY", "explicit-key")
    reload_settings()

    async with session_maker() as session:
        async with session.begin():
            await SettingsStore(session).set("api_key", "persisted-key")

        async with session.begin():
            effective = await SettingsStore(session).ensure_runtime_api_key()

    assert effective == "explicit-key"
    assert os.environ["SIFTARR_API_KEY"] == "explicit-key"
    assert get_settings().api_key == "explicit-key"


@pytest.mark.parametrize(
    ("saved", "expected"),
    [
        pytest.param(None, "https://compose-overseerr", id="env_kept_when_db_missing"),
        pytest.param("https://saved-overseerr", "https://saved-overseerr", id="db_overrides_env"),
    ],
)
async def test_load_into_environ_precedence(monkeypatch, session_maker, saved, expected):
    monkeypatch.setenv("OVERSEERR_URL", "https://compose-overseerr")
    reload_settings()

    async with session_maker() as session, session.begin():
        store = SettingsStore(session)
        if saved:
            await store.set("overseerr_url", saved)
        await store.load_into_environ()

    assert os.environ["OVERSEERR_URL"] == expected
    assert get_settings().overseerr_url == expected


@pytest.mark.asyncio
async def test_get_effective_dict_includes_sso_metadata_without_exposing_token(session_maker):
    async with session_maker() as session, session.begin():
        store = SettingsStore(session)
        await store.set("plex_claimed_id", "12345")
        await store.set("plex_username", "admin")
        await store.set("plex_thumb", "https://thumb")
        await store.set("plex_token", "secret-token")

        effective = await store.get_effective_dict()

    assert effective["plex_claimed_id"] == "12345"
    assert effective["plex_username"] == "admin"
    assert effective["plex_thumb"] == "https://thumb"
    assert effective["plex_token_present"] is True
    assert effective["plex_token"] == "********oken"
    assert "secret-token" not in effective.values()


@pytest.mark.asyncio
async def test_settings_backup_exports_only_restorable_non_secrets(session_maker):
    async with session_maker() as session, session.begin():
        store = SettingsStore(session)
        await store.set("overseerr_url", "https://overseerr")
        await store.set("overseerr_api_key", "secret")
        await store.set("plex_token", "token")
        await store.set("api_key", "generated")
        await store.set(PLEX_LAST_SYNC_SUCCESS_KEY, "2026-01-01T00:00:00+00:00")

        backup = await store.export_backup()

    assert backup["version"] == 1
    assert backup["settings"] == {"overseerr_url": "https://overseerr"}
    assert not (set(backup["settings"]) & SECRET_SETTING_KEYS)
    assert set(backup["settings"]) <= RESTORABLE_SETTING_KEYS


@pytest.mark.asyncio
async def test_settings_backup_restore_update_and_replace_non_secrets(session_maker):
    payload = '{"version":1,"settings":{"overseerr_url":"https://new","tz":"UTC"}}'
    async with session_maker() as session:
        async with session.begin():
            store = SettingsStore(session)
            await store.set("overseerr_url", "https://old")
            await store.set("prowlarr_url", "https://prowlarr")
            await store.set("overseerr_api_key", "keep-secret")
            result = await store.restore_backup(payload, mode="update")
        values = await SettingsStore(session).get_all()
        assert result == {"mode": "update", "restored": 2}
        assert values["overseerr_url"] == "https://new"
        assert values["prowlarr_url"] == "https://prowlarr"
        assert values["overseerr_api_key"] == "keep-secret"
        await session.rollback()

        async with session.begin():
            await SettingsStore(session).restore_backup(payload, mode="replace")
        values = await SettingsStore(session).get_all()

    assert values["overseerr_url"] == "https://new"
    assert "prowlarr_url" not in values
    assert values["overseerr_api_key"] == "keep-secret"


@pytest.mark.asyncio
async def test_settings_backup_replace_clears_omitted_restorable_env_and_preserves_secret_env(
    monkeypatch,
    session_maker,
):
    payload = '{"version":1,"settings":{"overseerr_url":"https://new"}}'
    monkeypatch.setenv("OVERSEERR_API_KEY", "compose-secret")

    async with session_maker() as session:
        async with session.begin():
            store = SettingsStore(session)
            await store.set("prowlarr_url", "https://old-prowlarr")
            await store.load_into_environ()

        assert os.environ["PROWLARR_URL"] == "https://old-prowlarr"
        assert get_settings().prowlarr_url == "https://old-prowlarr"

        async with session.begin():
            await SettingsStore(session).restore_backup(payload, mode="replace")

    assert os.environ["OVERSEERR_URL"] == "https://new"
    assert "PROWLARR_URL" not in os.environ
    assert os.environ["OVERSEERR_API_KEY"] == "compose-secret"
    assert get_settings().prowlarr_url is None
    assert get_settings().overseerr_api_key == "compose-secret"


@pytest.mark.asyncio
async def test_settings_backup_rejects_secret_keys(session_maker):
    async with session_maker() as session, session.begin():
        with pytest.raises(SettingsBackupError, match="non-restorable"):
            await SettingsStore(session).preview_backup(
                '{"version":1,"settings":{"overseerr_api_key":"secret"}}'
            )


@pytest.mark.asyncio
async def test_startup_loaded_api_key_authenticates_programmatic_requests(session_maker):
    async with session_maker() as session:
        async with session.begin():
            await SettingsStore(session).set("api_key", "persisted-api-key")
            await SettingsStore(session).set("plex_claimed_id", "admin-id")

        async with session.begin():
            await SettingsStore(session).ensure_runtime_api_key()

    class Request:
        session = {}
        scope = {"method": "GET", "path": "/settings/api/api-key"}
        headers = {"X-API-Key": "persisted-api-key", "accept": "application/json"}

    assert get_settings().api_key == "persisted-api-key"
    assert os.environ["PLEX_CLAIMED_ID"] == "admin-id"
    await require_auth(cast(Any, Request()))


def test_sync_timestamp_helpers_handle_never_stale_and_fresh():
    now = datetime(2026, 1, 2, 12, tzinfo=UTC)

    assert parse_sync_timestamp(None) is None
    assert parse_sync_timestamp("not-a-date") is None
    assert is_sync_timestamp_stale(None, now=now) is True
    assert is_sync_timestamp_stale(now - timedelta(hours=25), now=now) is True
    assert is_sync_timestamp_stale(now - timedelta(hours=2), now=now) is False

    serialized = serialize_sync_timestamp(now)
    assert parse_sync_timestamp(serialized) == now


@pytest.mark.asyncio
async def test_settings_store_records_and_checks_sync_staleness(session_maker):
    now = datetime(2026, 1, 2, 12, tzinfo=UTC)
    async with session_maker() as session:
        async with session.begin():
            store = SettingsStore(session)
            assert await store.is_sync_stale("plex", now=now) is True
            await store.record_sync_success("plex", now - timedelta(hours=1))

        persisted = await session.scalar(
            select(AppSetting).where(AppSetting.key == PLEX_LAST_SYNC_SUCCESS_KEY)
        )
        assert persisted is not None
        assert parse_sync_timestamp(persisted.value) == now - timedelta(hours=1)
        await session.rollback()

        async with session.begin():
            store = SettingsStore(session)
            assert await store.is_sync_stale("plex", now=now) is False


async def test_prepare_overseerr_import_uses_plex_username_for_requester():
    class FakeOverseerr:
        async def get_media_details(self, media_type, media_id):
            return {"title": "Movie", "releaseDate": "2020-01-01"}

    prepared = await prepare_overseerr_import(
        {
            "id": 7,
            "media": {"tmdbId": 11, "mediaType": "movie"},
            "requestedBy": {"plexUsername": "plex-user", "displayName": "Display"},
        },
        FakeOverseerr(),
        asyncio.Semaphore(1),
        {},
        asyncio.Lock(),
    )

    assert prepared is not None
    assert prepared.requester_username == "plex-user"
