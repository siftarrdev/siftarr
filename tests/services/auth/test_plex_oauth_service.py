"""Tests for PlexOAuthService."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from app.siftarr.services.auth.plex_oauth_service import PlexOAuthService


class TestBuildDeviceHeaders:
    """Tests for build_device_headers."""

    def test_returns_expected_headers(self):
        headers = PlexOAuthService.build_device_headers("test-uuid")
        assert headers["X-Plex-Client-Identifier"] == "test-uuid"
        assert headers["X-Plex-Product"] == "Siftarr"
        assert "X-Plex-Version" in headers
        assert headers["Accept"] == "application/json"


class TestRequestPin:
    """Tests for request_pin."""

    @patch("app.siftarr.services.auth.plex_oauth_service.get_shared_client")
    async def test_success(self, mock_get_client):
        """Should return the PIN data on success."""
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.json.return_value = {"id": 123, "code": "abc123"}
        mock_client.post.return_value = mock_response
        mock_get_client.return_value = mock_client

        result = await PlexOAuthService.request_pin("test-uuid")
        assert result == {"id": 123, "code": "abc123"}
        mock_client.post.assert_called_once()

    @patch("app.siftarr.services.auth.plex_oauth_service.get_shared_client")
    async def test_http_error_returns_none(self, mock_get_client):
        """Should return None when the HTTP request fails."""
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post.side_effect = httpx.HTTPError("boom")
        mock_get_client.return_value = mock_client

        result = await PlexOAuthService.request_pin("test-uuid")
        assert result is None


class TestCheckPin:
    """Tests for check_pin."""

    @patch("app.siftarr.services.auth.plex_oauth_service.get_shared_client")
    async def test_authorized_returns_auth_token(self, mock_get_client):
        """Should return the full response when authToken is present."""
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.json.return_value = {"id": 123, "authToken": "token123"}
        mock_client.get.return_value = mock_response
        mock_get_client.return_value = mock_client

        result = await PlexOAuthService.check_pin(123, "test-uuid")
        assert result == {"id": 123, "authToken": "token123"}

    @patch("app.siftarr.services.auth.plex_oauth_service.get_shared_client")
    async def test_pending_returns_no_auth_token(self, mock_get_client):
        """Should return response without authToken when still pending."""
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.json.return_value = {"id": 123}  # No authToken
        mock_client.get.return_value = mock_response
        mock_get_client.return_value = mock_client

        result = await PlexOAuthService.check_pin(123, "test-uuid")
        assert result is not None
        assert result == {"id": 123}
        assert "authToken" not in result

    @patch("app.siftarr.services.auth.plex_oauth_service.get_shared_client")
    async def test_http_error_returns_none(self, mock_get_client):
        """Should return None when the HTTP request fails."""
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get.side_effect = httpx.HTTPError("boom")
        mock_get_client.return_value = mock_client

        result = await PlexOAuthService.check_pin(123, "test-uuid")
        assert result is None


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

    @patch.object(PlexOAuthService, "get_user_identity")
    async def test_invalid_token_returns_none(self, mock_get_identity):
        """Should return None when get_user_identity returns None."""
        mock_get_identity.return_value = None
        result = await PlexOAuthService.validate_token("bad-token")
        assert result is None

    @patch.object(PlexOAuthService, "get_user_identity")
    async def test_missing_user_key_returns_none(self, mock_get_identity):
        """Should return None when the response lacks a 'user' key."""
        mock_get_identity.return_value = {"not_user": {}}
        result = await PlexOAuthService.validate_token("token-no-user")
        assert result is None
