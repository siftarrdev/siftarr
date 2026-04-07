"""Tests for ConnectionTester."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.arbitratarr.services.connection_tester import ConnectionTester, ConnectionTestResult


class TestConnectionTestResult:
    """Test cases for ConnectionTestResult."""

    def test_init(self):
        """Test ConnectionTestResult initialization."""
        result = ConnectionTestResult(
            success=True,
            message="Connected",
            details="Version 1.0",
        )
        assert result.success is True
        assert result.message == "Connected"
        assert result.details == "Version 1.0"

    def test_init_without_details(self):
        """Test ConnectionTestResult without details."""
        result = ConnectionTestResult(success=False, message="Failed")
        assert result.success is False
        assert result.message == "Failed"
        assert result.details is None


class TestConnectionTester:
    """Test cases for ConnectionTester."""

    @pytest.fixture
    def mock_settings(self):
        """Create mock settings."""
        settings = MagicMock()
        settings.overseerr_url = "http://localhost:5055"
        settings.overseerr_api_key = "test_key"
        settings.prowlarr_url = "http://localhost:9696"
        settings.prowlarr_api_key = "test_key"
        settings.qbittorrent_url = "http://localhost:8080"
        settings.qbittorrent_username = "admin"
        settings.qbittorrent_password = "admin123"
        return settings

    @pytest.mark.asyncio
    async def test_test_overseerr_no_url(self, mock_settings):
        """Test Overseerr test with no URL configured."""
        mock_settings.overseerr_url = ""

        result = await ConnectionTester.test_overseerr(mock_settings)

        assert result.success is False
        assert "URL is not configured" in result.message

    @pytest.mark.asyncio
    async def test_test_overseerr_no_api_key(self, mock_settings):
        """Test Overseerr test with no API key configured."""
        mock_settings.overseerr_api_key = ""

        result = await ConnectionTester.test_overseerr(mock_settings)

        assert result.success is False
        assert "API key is not configured" in result.message

    @pytest.mark.asyncio
    async def test_test_overseerr_success(self, mock_settings):
        """Test successful Overseerr connection."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"version": "1.0.0"}

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client_class.return_value = mock_client

            result = await ConnectionTester.test_overseerr(mock_settings)

            assert result.success is True
            assert result.details is not None
            assert "1.0.0" in result.details

    @pytest.mark.asyncio
    async def test_test_overseerr_unauthorized(self, mock_settings):
        """Test Overseerr with invalid API key."""
        mock_response = MagicMock()
        mock_response.status_code = 401

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client_class.return_value = mock_client

            result = await ConnectionTester.test_overseerr(mock_settings)

            assert result.success is False
            assert "Authentication failed" in result.message

    @pytest.mark.asyncio
    async def test_test_overseerr_http_error(self, mock_settings):
        """Test Overseerr with HTTP error."""
        mock_response = MagicMock()
        mock_response.status_code = 500

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client_class.return_value = mock_client

            result = await ConnectionTester.test_overseerr(mock_settings)

            assert result.success is False
            assert "HTTP Error: 500" in result.message

    @pytest.mark.asyncio
    async def test_test_overseerr_timeout(self, mock_settings):
        """Test Overseerr connection timeout."""
        import httpx

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(side_effect=httpx.TimeoutException("Timeout"))
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client_class.return_value = mock_client

            result = await ConnectionTester.test_overseerr(mock_settings)

            assert result.success is False
            assert "timeout" in result.message.lower()

    @pytest.mark.asyncio
    async def test_test_overseerr_request_error(self, mock_settings):
        """Test Overseerr with request error."""
        import httpx

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(side_effect=httpx.RequestError("Connection refused"))
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client_class.return_value = mock_client

            result = await ConnectionTester.test_overseerr(mock_settings)

            assert result.success is False
            assert "Connection failed" in result.message

    @pytest.mark.asyncio
    async def test_test_prowlarr_no_url(self, mock_settings):
        """Test Prowlarr test with no URL configured."""
        mock_settings.prowlarr_url = ""

        result = await ConnectionTester.test_prowlarr(mock_settings)

        assert result.success is False
        assert "URL is not configured" in result.message

    @pytest.mark.asyncio
    async def test_test_prowlarr_no_api_key(self, mock_settings):
        """Test Prowlarr test with no API key configured."""
        mock_settings.prowlarr_api_key = ""

        result = await ConnectionTester.test_prowlarr(mock_settings)

        assert result.success is False
        assert "API key is not configured" in result.message

    @pytest.mark.asyncio
    async def test_test_prowlarr_success(self, mock_settings):
        """Test successful Prowlarr connection."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"version": "2.0.0"}

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client_class.return_value = mock_client

            result = await ConnectionTester.test_prowlarr(mock_settings)

        assert result.success is True
        assert result.details is not None
        assert "2.0.0" in result.details

    @pytest.mark.asyncio
    async def test_test_prowlarr_unauthorized(self, mock_settings):
        """Test Prowlarr with invalid API key."""
        mock_response = MagicMock()
        mock_response.status_code = 401

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client_class.return_value = mock_client

            result = await ConnectionTester.test_prowlarr(mock_settings)

            assert result.success is False

    @pytest.mark.asyncio
    async def test_test_prowlarr_timeout(self, mock_settings):
        """Test Prowlarr connection timeout."""
        import httpx

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(side_effect=httpx.TimeoutException("Timeout"))
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client_class.return_value = mock_client

            result = await ConnectionTester.test_prowlarr(mock_settings)

            assert result.success is False

    @pytest.mark.asyncio
    async def test_test_qbittorrent_no_url(self, mock_settings):
        """Test qBittorrent test with no URL configured."""
        mock_settings.qbittorrent_url = ""

        result = await ConnectionTester.test_qbittorrent(mock_settings)

        assert result.success is False
        assert "URL is not configured" in result.message

    @pytest.mark.asyncio
    async def test_test_qbittorrent_no_username(self, mock_settings):
        """Test qBittorrent test with no username configured."""
        mock_settings.qbittorrent_username = ""

        result = await ConnectionTester.test_qbittorrent(mock_settings)

        assert result.success is False
        assert "username" in result.message.lower()

    @pytest.mark.asyncio
    async def test_test_qbittorrent_no_password(self, mock_settings):
        """Test qBittorrent test with no password configured."""
        mock_settings.qbittorrent_password = ""

        result = await ConnectionTester.test_qbittorrent(mock_settings)

        assert result.success is False
        assert "password" in result.message.lower()

    @pytest.mark.asyncio
    async def test_test_qbittorrent_success(self, mock_settings):
        """Test successful qBittorrent connection."""

        mock_client = MagicMock()
        mock_client.auth.log_in = MagicMock()
        mock_client.app.web_api_version = "v2.0"

        with (
            patch("qbittorrentapi.Client", return_value=mock_client),
            patch("asyncio.to_thread", AsyncMock()),
        ):
            result = await ConnectionTester.test_qbittorrent(mock_settings)

        assert result.success is True
        assert result.details is not None
        assert "Web API Version" in result.details

    @pytest.mark.asyncio
    async def test_test_qbittorrent_login_failed(self, mock_settings):
        """Test qBittorrent with invalid credentials."""
        import qbittorrentapi

        with patch("qbittorrentapi.Client") as mock_client_class:
            mock_client = MagicMock()
            mock_client.auth.log_in = MagicMock(side_effect=qbittorrentapi.LoginFailed("Invalid"))
            mock_client_class.return_value = mock_client

            result = await ConnectionTester.test_qbittorrent(mock_settings)

            assert result.success is False
            assert "Authentication failed" in result.message

    @pytest.mark.asyncio
    async def test_test_qbittorrent_version_check_failed(self, mock_settings):
        """Test qBittorrent when version check fails but login succeeds."""

        mock_client = MagicMock()
        mock_client.auth.log_in = MagicMock()
        mock_client.app.web_api_version = property(
            lambda self: (_ for _ in ()).throw(Exception("Failed"))
        )

        with (
            patch("qbittorrentapi.Client", return_value=mock_client),
            patch("asyncio.to_thread", AsyncMock()),
        ):
            result = await ConnectionTester.test_qbittorrent(mock_settings)

        assert result.success is True

    @pytest.mark.asyncio
    async def test_test_qbittorrent_connection_error(self, mock_settings):
        """Test qBittorrent with connection error."""
        with patch("qbittorrentapi.Client", side_effect=Exception("Connection refused")):
            result = await ConnectionTester.test_qbittorrent(mock_settings)

            assert result.success is False
            assert "Connection failed" in result.message
