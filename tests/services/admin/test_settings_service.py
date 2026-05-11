import os

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.siftarr.config import PLACEHOLDER_API_KEY, get_settings, reload_settings
from app.siftarr.models import Base
from app.siftarr.models.app_setting import AppSetting
from app.siftarr.services.admin.settings_service import SettingsStore


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
    reload_settings()
    yield
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
