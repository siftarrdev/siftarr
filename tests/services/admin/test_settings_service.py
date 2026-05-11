import os
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.siftarr.config import PLACEHOLDER_API_KEY, get_settings, reload_settings
from app.siftarr.models import Base
from app.siftarr.models.app_setting import AppSetting
from app.siftarr.services.admin.settings_service import (
    PLEX_LAST_SYNC_SUCCESS_KEY,
    SettingsStore,
    is_sync_timestamp_stale,
    parse_sync_timestamp,
    serialize_sync_timestamp,
)
from app.siftarr.services.auth_service import require_auth


@pytest.fixture
async def session_maker():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


@pytest.fixture(autouse=True)
def _clean_api_key_env(monkeypatch):
    monkeypatch.delenv("SIFTARR_API_KEY", raising=False)
    monkeypatch.delenv("PLEX_CLAIMED_ID", raising=False)
    reload_settings()
    yield
    for key in ("SIFTARR_API_KEY", "PLEX_CLAIMED_ID", "PLEX_USERNAME", "PLEX_THUMB", "PLEX_TOKEN"):
        os.environ.pop(key, None)
    reload_settings()


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


@pytest.mark.asyncio
async def test_load_into_environ_preserves_process_env_when_db_value_missing(
    monkeypatch,
    session_maker,
):
    monkeypatch.setenv("OVERSEERR_URL", "https://compose-overseerr")
    reload_settings()

    async with session_maker() as session, session.begin():
        await SettingsStore(session).load_into_environ()

    assert os.environ["OVERSEERR_URL"] == "https://compose-overseerr"
    assert get_settings().overseerr_url == "https://compose-overseerr"


@pytest.mark.asyncio
async def test_load_into_environ_db_values_override_process_env(
    monkeypatch,
    session_maker,
):
    monkeypatch.setenv("OVERSEERR_URL", "https://compose-overseerr")
    reload_settings()

    async with session_maker() as session, session.begin():
        store = SettingsStore(session)
        await store.set("overseerr_url", "https://saved-overseerr")
        await store.load_into_environ()

    assert os.environ["OVERSEERR_URL"] == "https://saved-overseerr"
    assert get_settings().overseerr_url == "https://saved-overseerr"


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
async def test_startup_loaded_api_key_authenticates_programmatic_requests(session_maker):
    async with session_maker() as session:
        async with session.begin():
            await SettingsStore(session).set("api_key", "persisted-api-key")
            await SettingsStore(session).set("plex_claimed_id", "admin-id")

        async with session.begin():
            await SettingsStore(session).ensure_runtime_api_key()

    class Request:
        session = {}
        scope = {"method": "GET", "path": "/settings/api/connections"}
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
