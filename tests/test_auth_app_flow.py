"""App-level Plex SSO and API-key auth flow tests."""

import os
from collections.abc import AsyncGenerator, Iterator
from typing import Any, cast
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.siftarr import main as main_module
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

    async def ensure_runtime_api_key(self) -> None:
        return None


class AsyncContext:
    def __init__(self, value=None) -> None:
        self.value = value or MagicMock()

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, exc_type, exc, tb):
        return False


class StartupSession:
    def begin(self):
        return AsyncContext(self)


class StartupSessionMaker:
    def __call__(self):
        return AsyncContext(StartupSession())


class NoopScheduler:
    def __init__(self, *args, **kwargs) -> None:
        pass

    def start(self) -> None:
        return None

    def stop(self) -> None:
        return None

    def run_startup_catchup_syncs(self):
        return None


async def _fake_db() -> AsyncGenerator[MagicMock]:
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
    assert first.json()["redirect_url"] == "/auth/initial-plex-sync?next=%2Fsettings"
    assert MemorySettingsStore.values["plex_claimed_id"] == "admin-id"

    gated = client.get("/settings", headers={"accept": "text/html"}, follow_redirects=False)
    assert gated.status_code == 303
    assert gated.headers["location"] == "/auth/initial-plex-sync?next=%2Fsettings"

    completed = client.post("/auth/initial-plex-sync/complete")
    assert completed.status_code == 200
    assert completed.json()["redirect_url"] == "/settings"

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


def test_startup_ensures_default_rules_after_runtime_api_key(monkeypatch):
    calls: list[str] = []

    class StartupSettingsStore(MemorySettingsStore):
        async def ensure_runtime_api_key(self) -> None:
            calls.append("api_key")

    class StartupRuleService:
        def __init__(self, db) -> None:
            del db

        async def ensure_default_rules(self) -> None:
            calls.append("rules")

    async def init_db() -> None:
        calls.append("init_db")
        cast(Any, main_module.db_mod).async_session_maker = StartupSessionMaker()

    async def close_client() -> None:
        return None

    monkeypatch.setenv("SECRET_KEY", "test-secret")
    monkeypatch.setenv("SIFTARR_DB_PATH", ":memory:")
    reload_settings()
    monkeypatch.setattr(main_module, "_ensure_db_directory", lambda: None)
    monkeypatch.setattr(main_module.db_mod, "init_db", init_db)
    monkeypatch.setattr(main_module, "SettingsStore", StartupSettingsStore)
    monkeypatch.setattr(main_module, "RuleService", StartupRuleService)
    monkeypatch.setattr(main_module, "SchedulerService", NoopScheduler)
    monkeypatch.setattr(main_module, "close_shared_client", close_client)

    with TestClient(create_app(), raise_server_exceptions=True):
        pass

    assert calls[:3] == ["init_db", "api_key", "rules"]
