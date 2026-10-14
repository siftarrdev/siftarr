"""Tests for auth_service module."""

from unittest.mock import patch

import pytest
from fastapi import HTTPException, Request

from app.siftarr.services.auth_service import (
    BrowserAuthRequired,
    InitialPlexSyncRequired,
    _extract_api_key,
    get_session_user,
    require_auth,
)


def make_request(**scope) -> Request:
    return Request(scope={"type": "http", **scope})


@pytest.fixture
def auth_settings():
    with patch("app.siftarr.services.auth_service.get_settings") as mock_get_settings:
        settings = mock_get_settings.return_value
        settings.auth_enabled = True
        settings.api_key = "secret"
        yield settings


@pytest.mark.parametrize(
    ("headers", "expected"),
    [
        ([(b"x-api-key", b"my-key")], "my-key"),
        ([(b"authorization", b"Bearer my-token")], "my-token"),
        ([], None),
    ],
    ids=["x_api_key", "bearer", "absent"],
)
def test_extract_api_key(headers, expected):
    assert _extract_api_key(make_request(headers=headers)) == expected


@pytest.mark.parametrize(
    "session",
    [{}, {"other_key": "value"}],
    ids=["empty_session", "missing_user_id"],
)
def test_get_session_user_without_user_returns_none(session):
    assert get_session_user(make_request(session=session)) is None


def test_get_session_user_returns_user():
    request = make_request(session={"plex_user_id": "123", "plex_username": "testuser"})

    user = get_session_user(request)

    assert user is not None
    assert user["plex_user_id"] == "123"
    assert user["plex_username"] == "testuser"


def test_stale_session_is_cleared(monkeypatch):
    """Session user id must match the claimed admin id."""
    monkeypatch.setenv("PLEX_CLAIMED_ID", "admin")
    session = {"plex_user_id": "other", "plex_username": "intruder"}

    assert get_session_user(make_request(session=session)) is None
    assert session == {}


async def test_auth_disabled_passes(auth_settings):
    auth_settings.auth_enabled = False
    await require_auth(make_request(session={}, headers=[(b"accept", b"application/json")]))


@pytest.mark.parametrize(
    "scope",
    [
        {"session": {"plex_user_id": "123"}},
        {"session": {}, "headers": [(b"x-api-key", b"secret")]},
        {"session": {}, "headers": [(b"authorization", b"Bearer secret")]},
        {
            "method": "GET",
            "path": "/settings/api/rescan-plex/stream",
            "session": {"plex_user_id": "123", "initial_plex_sync_required": True},
            "headers": [(b"accept", b"text/event-stream")],
        },
    ],
    ids=["session", "x_api_key", "bearer_api_key", "initial_sync_allows_sync_stream"],
)
async def test_require_auth_accepts(auth_settings, scope):
    await require_auth(make_request(**scope))


@pytest.mark.parametrize(
    ("api_key", "headers", "path"),
    [
        ("secret", [(b"accept", b"application/json")], "/api/x"),
        ("correct-key", [(b"x-api-key", b"wrong-key")], "/api/x"),
        ("dev-key-change-me", [(b"x-api-key", b"dev-key-change-me")], "/api/x"),
        ("secret", [(b"accept", b"*/*")], "/requests/1/details"),
    ],
    ids=["no_credentials", "wrong_key", "placeholder_key_rejected", "api_route_wildcard_accept"],
)
async def test_require_auth_rejects_with_401(auth_settings, api_key, headers, path):
    auth_settings.api_key = api_key
    request = make_request(method="GET", path=path, session={}, headers=headers)

    with pytest.raises(HTTPException) as exc:
        await require_auth(request)

    assert exc.value.status_code == 401


@pytest.mark.parametrize(
    ("session", "headers", "path", "error"),
    [
        ({}, [(b"accept", b"text/html")], "/dashboard", BrowserAuthRequired),
        (
            {"plex_user_id": "123", "initial_plex_sync_required": True},
            [(b"accept", b"text/html")],
            "/dashboard",
            InitialPlexSyncRequired,
        ),
    ],
    ids=["browser_redirects_to_login", "initial_sync_blocks_dashboard"],
)
async def test_require_auth_browser_gates(auth_settings, session, headers, path, error):
    request = make_request(
        method="GET", path=path, query_string=b"tab=all", session=session, headers=headers
    )

    with pytest.raises(error):
        await require_auth(request)
