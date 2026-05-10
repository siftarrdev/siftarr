"""Tests for auth_service module."""

from unittest.mock import patch

import pytest
from fastapi import HTTPException, Request

from app.siftarr.services.auth_service import (
    _extract_api_key,
    get_session_user,
    require_auth,
)


class TestExtractApiKey:
    """Tests for _extract_api_key."""

    def test_x_api_key_header(self):
        """Should extract from X-API-Key header."""
        request = Request(
            scope={
                "type": "http",
                "headers": [(b"x-api-key", b"my-key")],
            }
        )
        assert _extract_api_key(request) == "my-key"

    def test_authorization_bearer(self):
        """Should extract from Authorization: Bearer header."""
        request = Request(
            scope={
                "type": "http",
                "headers": [(b"authorization", b"Bearer my-token")],
            }
        )
        assert _extract_api_key(request) == "my-token"

    def test_no_header_returns_none(self):
        """Should return None when no auth header present."""
        request = Request(scope={"type": "http", "headers": []})
        assert _extract_api_key(request) is None


class TestGetSessionUser:
    """Tests for get_session_user."""

    def test_no_session_returns_none(self):
        """Should return None when no session data."""
        request = Request(scope={"type": "http", "session": {}})
        assert get_session_user(request) is None

    def test_with_session_returns_user(self):
        """Should return user dict from session."""
        request = Request(
            scope={
                "type": "http",
                "session": {
                    "plex_user_id": "123",
                    "plex_username": "testuser",
                    "plex_thumb": "http://example.com/thumb.jpg",
                },
            }
        )
        user = get_session_user(request)
        assert user is not None
        assert user["plex_user_id"] == "123"
        assert user["plex_username"] == "testuser"

    def test_session_without_user_id_returns_none(self):
        """Should return None when plex_user_id is missing from session."""
        request = Request(
            scope={
                "type": "http",
                "session": {"other_key": "value"},
            }
        )
        assert get_session_user(request) is None


class TestRequireAuth:
    """Tests for require_auth."""

    @patch("app.siftarr.services.auth_service.get_settings")
    async def test_auth_disabled_passes(self, mock_get_settings):
        """Should pass when auth is disabled."""
        mock_get_settings.return_value.auth_enabled = False
        request = Request(scope={"type": "http", "session": {}})
        await require_auth(request)

    @patch("app.siftarr.services.auth_service.get_settings")
    async def test_valid_session_passes(self, mock_get_settings):
        """Should pass when valid session exists."""
        mock_get_settings.return_value.auth_enabled = True
        request = Request(
            scope={
                "type": "http",
                "session": {"plex_user_id": "123"},
            }
        )
        await require_auth(request)

    @patch("app.siftarr.services.auth_service.get_settings")
    async def test_valid_api_key_passes(self, mock_get_settings):
        """Should pass when valid API key is provided."""
        mock_get_settings.return_value.auth_enabled = True
        mock_get_settings.return_value.api_key = "secret"
        request = Request(
            scope={
                "type": "http",
                "session": {},
                "headers": [(b"x-api-key", b"secret")],
            }
        )
        await require_auth(request)

    @patch("app.siftarr.services.auth_service.get_settings")
    async def test_valid_bearer_api_key_passes(self, mock_get_settings):
        """Should pass when valid API key is provided via Authorization header."""
        mock_get_settings.return_value.auth_enabled = True
        mock_get_settings.return_value.api_key = "secret"
        request = Request(
            scope={
                "type": "http",
                "session": {},
                "headers": [(b"authorization", b"Bearer secret")],
            }
        )
        await require_auth(request)

    @patch("app.siftarr.services.auth_service.get_settings")
    async def test_no_session_no_key_raises(self, mock_get_settings):
        """Should raise 401 when no session and no API key."""
        mock_get_settings.return_value.auth_enabled = True
        mock_get_settings.return_value.api_key = "secret"
        request = Request(
            scope={
                "type": "http",
                "session": {},
                "headers": [],
            }
        )
        with pytest.raises(HTTPException) as exc:
            await require_auth(request)
        assert exc.value.status_code == 401

    @patch("app.siftarr.services.auth_service.get_settings")
    async def test_wrong_api_key_raises(self, mock_get_settings):
        """Should raise 401 when API key doesn't match."""
        mock_get_settings.return_value.auth_enabled = True
        mock_get_settings.return_value.api_key = "correct-key"
        request = Request(
            scope={
                "type": "http",
                "session": {},
                "headers": [(b"x-api-key", b"wrong-key")],
            }
        )
        with pytest.raises(HTTPException) as exc:
            await require_auth(request)
        assert exc.value.status_code == 401
