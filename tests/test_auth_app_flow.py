"""App-level Plex SSO and API-key auth flow tests."""

import os
from collections.abc import AsyncGenerator, Iterator
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.siftarr.config import reload_settings
from app.siftarr.main import create_app
from app.siftarr.routers import auth_router
from app.siftarr.routers import settings as settings_router


class MemorySettingsStore:
    values: dict[str, str] = {}

    def __init__(self, db) -> None:
        del db

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def set(self, key: str, value: str) -> None:
        self.values[key] = value

    async def load_into_environ(self) -> None:
        import os

        for key, value in self.values.items():
            os.environ[key.upper()] = value
        reload_settings()

    async def get_effective_dict(self) -> dict[str, str | bool | None]:
        return {
            "overseerr_url": "",
            "overseerr_api_key": "",
            "prowlarr_url": "",
            "prowlarr_api_key": "",
            "qbittorrent_url": "",
            "qbittorrent_api_key": "",
            "plex_url": "",
            "plex_token": "",
            "tz": "UTC",
            "plex_username": self.values.get("plex_username"),
            "plex_thumb": self.values.get("plex_thumb"),
            "plex_token_present": bool(self.values.get("plex_token")),
        }


async def _fake_db() -> AsyncGenerator[MagicMock, None]:
    yield MagicMock()


@pytest.fixture
def client(monkeypatch) -> Iterator[TestClient]:
    monkeypatch.setenv("SECRET_KEY", "test-secret")
    monkeypatch.setenv("SIFTARR_API_KEY", "valid-api-key")
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.delenv("PLEX_CLAIMED_ID", raising=False)
    reload_settings()
    MemorySettingsStore.values = {}
    monkeypatch.setattr(auth_router, "SettingsStore", MemorySettingsStore)
    monkeypatch.setattr(settings_router, "SettingsStore", MemorySettingsStore)
    app = create_app()
    app.dependency_overrides[auth_router.get_db] = _fake_db
    app.dependency_overrides[settings_router.get_db] = _fake_db
    try:
        yield TestClient(app, raise_server_exceptions=False)
    finally:
        for key in ("PLEX_CLAIMED_ID", "PLEX_USERNAME", "PLEX_THUMB", "PLEX_TOKEN"):
            os.environ.pop(key, None)
        reload_settings()


def test_browser_routes_redirect_but_api_routes_return_401_or_accept_api_key(client):
    for path in ["/", "/dashboard", "/rules", "/settings"]:
        response = client.get(path, headers={"accept": "text/html"}, follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"].startswith(
            f"/auth/login?next={path.replace('/', '%2F')}"
        )

    api_response = client.get("/settings/api/connections", headers={"accept": "application/json"})
    assert api_response.status_code == 401

    authed_api_response = client.get(
        "/settings/api/connections",
        headers={"accept": "application/json", "X-API-Key": "valid-api-key"},
    )
    assert authed_api_response.status_code == 200
    assert authed_api_response.json()["tz"] == "UTC"


def test_plex_claim_relogin_denial_logout_and_next_flow(client, monkeypatch):
    async def validate_token(token: str):
        return {
            "admin-token": {"id": "admin-id", "username": "admin", "thumb": "https://thumb"},
            "other-token": {"id": "other-id", "username": "other", "thumb": ""},
        }.get(token)

    monkeypatch.setattr(auth_router.PlexOAuthService, "validate_token", validate_token)

    first = client.post("/auth/plex", json={"authToken": "admin-token", "next": "/settings"})
    assert first.status_code == 200
    assert first.json()["redirect_url"] == "/settings"
    assert MemorySettingsStore.values["plex_claimed_id"] == "admin-id"

    relogin = client.post("/auth/plex", json={"authToken": "admin-token", "next": "https://evil"})
    assert relogin.status_code == 200
    assert relogin.json()["redirect_url"] == "/"

    client.cookies.clear()
    denied = client.post("/auth/plex", json={"authToken": "other-token"})
    assert denied.status_code == 403
    assert denied.json()["detail"] == auth_router.ADMIN_LOGIN_MESSAGE

    denied_page = client.get("/auth/login?denied=1")
    assert denied_page.status_code == 200
    assert auth_router.ADMIN_LOGIN_MESSAGE in denied_page.text

    logout = client.get("/auth/logout", follow_redirects=False)
    assert logout.status_code in {303, 307}
    assert logout.headers["location"] == "/auth/login"


def test_stale_non_admin_session_is_cleared_and_redirected(client, monkeypatch):
    async def validate_token(token: str):
        del token
        return {"id": "admin-id", "username": "admin", "thumb": ""}

    monkeypatch.setattr(auth_router.PlexOAuthService, "validate_token", validate_token)
    assert client.post("/auth/plex", json={"authToken": "admin-token"}).status_code == 200

    os.environ["PLEX_CLAIMED_ID"] = "different-admin"
    reload_settings()

    response = client.get("/dashboard", headers={"accept": "text/html"}, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"].startswith("/auth/login")


def test_session_cookie_survives_restart_like_app_reload(monkeypatch, tmp_path):
    async def validate_token(token: str):
        del token
        return {"id": "admin-id", "username": "admin", "thumb": "https://thumb"}

    monkeypatch.delenv("SECRET_KEY", raising=False)
    monkeypatch.setenv("SIFTARR_SECRET_KEY_FILE", str(tmp_path / "session_secret"))
    monkeypatch.setenv("SIFTARR_API_KEY", "valid-api-key")
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.delenv("PLEX_CLAIMED_ID", raising=False)
    reload_settings()
    MemorySettingsStore.values = {}
    monkeypatch.setattr(auth_router, "SettingsStore", MemorySettingsStore)
    monkeypatch.setattr(settings_router, "SettingsStore", MemorySettingsStore)
    monkeypatch.setattr(auth_router.PlexOAuthService, "validate_token", validate_token)

    try:
        first_app = create_app()
        first_app.dependency_overrides[auth_router.get_db] = _fake_db
        first_client = TestClient(first_app, raise_server_exceptions=False)
        login = first_client.post("/auth/plex", json={"authToken": "admin-token"})
        assert login.status_code == 200
        assert first_client.get("/auth/me").status_code == 200

        reload_settings()
        second_app = create_app()
        second_app.dependency_overrides[auth_router.get_db] = _fake_db
        second_client = TestClient(second_app, raise_server_exceptions=False)
        second_client.cookies.update(first_client.cookies)

        response = second_client.get("/auth/me")
        assert response.status_code == 200
        assert response.json()["username"] == "admin"
    finally:
        for key in ("PLEX_CLAIMED_ID", "PLEX_USERNAME", "PLEX_THUMB", "PLEX_TOKEN"):
            os.environ.pop(key, None)
        reload_settings()
