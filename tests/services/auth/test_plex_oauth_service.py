"""Tests for PlexOAuthService."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.siftarr.services.auth.plex_oauth_service import PlexOAuthService


class TestGetUserIdentity:
    """Tests for get_user_identity."""

    @patch("app.siftarr.services.auth.plex_oauth_service.get_shared_client")
    async def test_valid_token_returns_user_data(self, mock_get_client):
        """Should return user data for a valid token."""
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.json.return_value = {
            "user": {
                "id": 1,
                "username": "testuser",
                "email": "test@example.com",
                "thumb": "http://example.com/thumb.jpg",
            }
        }
        mock_client.get.return_value = mock_response
        mock_get_client.return_value = mock_client

        result = await PlexOAuthService.get_user_identity("valid-token")
        assert result is not None
        assert result["user"]["username"] == "testuser"

    @patch("app.siftarr.services.auth.plex_oauth_service.get_shared_client")
    async def test_invalid_token_returns_none(self, mock_get_client):
        """Should return None for an invalid token."""
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get.side_effect = httpx.HTTPError("unauthorized")
        mock_get_client.return_value = mock_client

        result = await PlexOAuthService.get_user_identity("bad-token")
        assert result is None


class TestValidateToken:
    """Tests for validate_token."""

    @patch.object(PlexOAuthService, "get_user_identity")
    async def test_valid_token_returns_user(self, mock_get_identity):
        """Should return extracted user info for a valid token."""
        mock_get_identity.return_value = {
            "user": {
                "id": 1,
                "username": "testuser",
                "thumb": "http://example.com/thumb.jpg",
            }
        }
        result = await PlexOAuthService.validate_token("valid-token")
        assert result == {
            "id": 1,
            "username": "testuser",
            "thumb": "http://example.com/thumb.jpg",
        }

    @pytest.mark.parametrize(
        "identity",
        [None, {"not_user": {}}],
        ids=["identity_lookup_failed", "missing_user_key"],
    )
    @patch.object(PlexOAuthService, "get_user_identity")
    async def test_unusable_identity_returns_none(self, mock_get_identity, identity):
        mock_get_identity.return_value = identity
        assert await PlexOAuthService.validate_token("bad-token") is None
