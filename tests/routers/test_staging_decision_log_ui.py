"""Tests for the Rules-area staging decision-log UI."""

import json
from base64 import b64encode
from collections.abc import AsyncGenerator, Iterator
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner

from app.siftarr.config import reload_settings
from app.siftarr.main import create_app
from app.siftarr.routers import auth_router
from app.siftarr.routers import settings as settings_router


class MemorySettingsStore:
    async def get(self, key: str) -> str | None:
        return None

    async def set(self, key: str, value: str) -> None:
        return None

    async def load_into_environ(self) -> None:
        return None

    async def ensure_runtime_api_key(self) -> None:
        return None


async def _fake_db() -> AsyncGenerator[MagicMock]:
    yield MagicMock()


@pytest.fixture
def client(monkeypatch) -> Iterator[TestClient]:
    monkeypatch.setenv("SECRET_KEY", "test-secret")
    monkeypatch.setenv("SIFTARR_API_KEY", "valid-api-key")
    monkeypatch.setenv("AUTH_ENABLED", "true")
    reload_settings()
    monkeypatch.setattr(auth_router, "SettingsStore", MemorySettingsStore)
    monkeypatch.setattr(settings_router, "SettingsStore", MemorySettingsStore)
    app = create_app()
    app.dependency_overrides[auth_router.get_db] = _fake_db
    app.dependency_overrides[settings_router.get_db] = _fake_db
    yield TestClient(app, raise_server_exceptions=False)
    reload_settings()


def test_decision_log_page_is_protected(client):
    response = client.get(
        "/rules/decision-log", headers={"accept": "text/html"}, follow_redirects=False
    )

    assert response.status_code == 303
    assert response.headers["location"].startswith("/auth/login?next=%2Frules%2Fdecision-log")


def test_decision_log_page_renders_with_session(client):
    session_data = b64encode(json.dumps({"plex_user_id": "admin-id"}).encode("utf-8"))
    client.cookies.set("session", TimestampSigner("test-secret").sign(session_data).decode("utf-8"))

    response = client.get("/rules/decision-log")

    assert response.status_code == 200
    assert "Staging Decision Log" in response.text
    assert "/static/js/staging_decision_log.js" in response.text
    assert 'name="date_from"' in response.text
