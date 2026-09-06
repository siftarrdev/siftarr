"""Session cookie flags must follow the HTTPS deployment setting."""

from http.cookies import SimpleCookie

import httpx
import pytest
from fastapi import Request

from app.siftarr import main
from app.siftarr.config import Settings


@pytest.mark.parametrize("secure", [False, True])
async def test_session_cookie_secure_flag(monkeypatch, secure):
    monkeypatch.setenv("SIFTARR_SESSION_HTTPS_ONLY", str(secure).lower())
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    settings = Settings(secret_key="test-secret")
    monkeypatch.setattr(main, "get_settings", lambda: settings)
    app = main.create_app()

    @app.get("/test-session")
    async def set_session(request: Request):
        request.session["test"] = "value"
        return {"ok": True}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://test"
    ) as client:
        response = await client.get("/test-session")

    cookie = SimpleCookie(response.headers["set-cookie"])["session"]
    assert bool(cookie["secure"]) is secure
    assert cookie["httponly"]
    assert cookie["samesite"] == "lax"
    assert cookie["max-age"] == str(86400 * 30)
