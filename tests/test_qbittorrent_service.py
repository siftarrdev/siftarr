"""Tests for QbittorrentService."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.arbitratarr.services.qbittorrent_service import MediaCategory, QbittorrentService


class TestMediaCategory:
    """Test cases for MediaCategory enum."""

    def test_movies_value(self):
        """Test MediaCategory.MOVIES value."""
        assert MediaCategory.MOVIES == "radarr"

    def test_tv_value(self):
        """Test MediaCategory.TV value."""
        assert MediaCategory.TV == "sonarr"


class TestQbittorrentServiceUnit:
    """Unit tests for QbittorrentService."""

    def test_client_property_creates_client(self):
        """Test client property creates qbittorrent client when accessed."""
        with patch("app.arbitratarr.config.get_settings") as mock_get_settings:
            mock_settings = MagicMock()
            mock_settings.qbittorrent_url = "http://localhost:8080"
            mock_settings.qbittorrent_username = "admin"
            mock_settings.qbittorrent_password = "admin123"
            mock_get_settings.return_value = mock_settings

            with patch("qbittorrentapi.Client") as mock_client_class:
                service = QbittorrentService()
                _ = service.client

                mock_client_class.assert_called_once_with(
                    host="http://localhost:8080",
                    username="admin",
                    password="admin123",
                )

    def test_client_property_reuses_client(self):
        """Test client property reuses existing client."""
        with patch("app.arbitratarr.config.get_settings") as mock_get_settings:
            mock_settings = MagicMock()
            mock_settings.qbittorrent_url = "http://localhost:8080"
            mock_settings.qbittorrent_username = "admin"
            mock_settings.qbittorrent_password = "admin123"
            mock_get_settings.return_value = mock_settings

            service = QbittorrentService()
            mock_client = MagicMock()
            service._client = mock_client

            assert service.client is mock_client

    @pytest.mark.asyncio
    async def test_authenticate_success(self):
        """Test successful authentication."""
        with patch("app.arbitratarr.config.get_settings") as mock_get_settings:
            mock_settings = MagicMock()
            mock_settings.qbittorrent_url = "http://localhost:8080"
            mock_settings.qbittorrent_username = "admin"
            mock_settings.qbittorrent_password = "admin123"
            mock_get_settings.return_value = mock_settings

            service = QbittorrentService()
            mock_auth = MagicMock()
            mock_auth.log_in = MagicMock()

            mock_client = MagicMock()
            mock_client.auth = mock_auth
            service._client = mock_client

            with patch("asyncio.to_thread", AsyncMock()):
                result = await service.authenticate()
                assert result is True

    @pytest.mark.asyncio
    async def test_authenticate_failure(self):
        """Test failed authentication."""
        import qbittorrentapi

        with patch("app.arbitratarr.config.get_settings") as mock_get_settings:
            mock_settings = MagicMock()
            mock_settings.qbittorrent_url = "http://localhost:8080"
            mock_settings.qbittorrent_username = "admin"
            mock_settings.qbittorrent_password = "admin123"
            mock_get_settings.return_value = mock_settings

            service = QbittorrentService()
            mock_client = MagicMock()
            service._client = mock_client

            with patch("asyncio.to_thread", side_effect=qbittorrentapi.LoginFailed("Invalid credentials")):
                result = await service.authenticate()
                assert result is False

    @pytest.mark.asyncio
    async def test_ensure_category_exists_already_exists(self):
        """Test ensure_category_exists when category exists."""
        with patch("app.arbitratarr.config.get_settings") as mock_get_settings:
            mock_settings = MagicMock()
            mock_settings.qbittorrent_url = "http://localhost:8080"
            mock_settings.qbittorrent_username = "admin"
            mock_settings.qbittorrent_password = "admin123"
            mock_get_settings.return_value = mock_settings

            service = QbittorrentService()
            mock_client = MagicMock()
            mock_client.torrents_categories = {"radarr", "sonarr"}
            service._client = mock_client

            with patch("asyncio.to_thread", AsyncMock(return_value=mock_client.torrents_categories)):
                result = await service.ensure_category_exists("radarr")
                assert result is True

    @pytest.mark.asyncio
    async def test_ensure_category_exists_error(self):
        """Test ensure_category_exists with error."""
        with patch("app.arbitratarr.config.get_settings") as mock_get_settings:
            mock_settings = MagicMock()
            mock_settings.qbittorrent_url = "http://localhost:8080"
            mock_settings.qbittorrent_username = "admin"
            mock_settings.qbittorrent_password = "admin123"
            mock_get_settings.return_value = mock_settings

            service = QbittorrentService()
            mock_client = MagicMock()
            service._client = mock_client

            with patch("asyncio.to_thread", side_effect=Exception("Connection error")):
                result = await service.ensure_category_exists("radarr")
                assert result is False

    @pytest.mark.asyncio
    async def test_get_torrent_info_found(self):
        """Test getting torrent info for existing torrent."""
        with patch("app.arbitratarr.config.get_settings") as mock_get_settings:
            mock_settings = MagicMock()
            mock_settings.qbittorrent_url = "http://localhost:8080"
            mock_settings.qbittorrent_username = "admin"
            mock_settings.qbittorrent_password = "admin123"
            mock_get_settings.return_value = mock_settings

            service = QbittorrentService()
            mock_torrent = MagicMock()
            mock_torrent.hash = "abc123"
            mock_torrent.name = "Test.Torrent"
            mock_torrent.size = 1024
            mock_torrent.progress = 0.5
            mock_torrent.state = "downloading"
            mock_torrent.category = "radarr"
            mock_torrent.ratio = 0.1
            mock_torrent.added_on = 1234567890
            mock_torrent.completed_on = 1234567900
            mock_torrent.download_location = "/downloads"

            mock_client = MagicMock()
            mock_client.torrents_info = MagicMock(return_value=[mock_torrent])
            service._client = mock_client

            with patch("asyncio.to_thread", AsyncMock(return_value=[mock_torrent])):
                result = await service.get_torrent_info("abc123")

                assert result["hash"] == "abc123"
                assert result["name"] == "Test.Torrent"

    @pytest.mark.asyncio
    async def test_get_torrent_info_not_found(self):
        """Test getting torrent info for non-existent torrent."""
        with patch("app.arbitratarr.config.get_settings") as mock_get_settings:
            mock_settings = MagicMock()
            mock_settings.qbittorrent_url = "http://localhost:8080"
            mock_settings.qbittorrent_username = "admin"
            mock_settings.qbittorrent_password = "admin123"
            mock_get_settings.return_value = mock_settings

            service = QbittorrentService()
            mock_client = MagicMock()
            mock_client.torrents_info = MagicMock(return_value=[])
            service._client = mock_client

            with patch("asyncio.to_thread", AsyncMock(return_value=[])):
                result = await service.get_torrent_info("nonexistent")
                assert result is None

    @pytest.mark.asyncio
    async def test_get_torrents_by_category(self):
        """Test getting torrents by category."""
        with patch("app.arbitratarr.config.get_settings") as mock_get_settings:
            mock_settings = MagicMock()
            mock_settings.qbittorrent_url = "http://localhost:8080"
            mock_settings.qbittorrent_username = "admin"
            mock_settings.qbittorrent_password = "admin123"
            mock_get_settings.return_value = mock_settings

            service = QbittorrentService()
            mock_torrent1 = MagicMock()
            mock_torrent1.hash = "abc"
            mock_torrent1.name = "Torrent 1"
            mock_torrent1.size = 1024
            mock_torrent1.progress = 1.0
            mock_torrent1.state = "seeding"

            mock_torrent2 = MagicMock()
            mock_torrent2.hash = "def"
            mock_torrent2.name = "Torrent 2"
            mock_torrent2.size = 2048
            mock_torrent2.progress = 0.5
            mock_torrent2.state = "downloading"

            mock_client = MagicMock()
            service._client = mock_client

            with patch("asyncio.to_thread", AsyncMock(return_value=[mock_torrent1, mock_torrent2])):
                result = await service.get_torrents_by_category("radarr")

                assert len(result) == 2
                assert result[0]["hash"] == "abc"

    @pytest.mark.asyncio
    async def test_get_torrents_by_category_error(self):
        """Test getting torrents by category with error."""
        with patch("app.arbitratarr.config.get_settings") as mock_get_settings:
            mock_settings = MagicMock()
            mock_settings.qbittorrent_url = "http://localhost:8080"
            mock_settings.qbittorrent_username = "admin"
            mock_settings.qbittorrent_password = "admin123"
            mock_get_settings.return_value = mock_settings

            service = QbittorrentService()
            mock_client = MagicMock()
            service._client = mock_client

            with patch("asyncio.to_thread", side_effect=Exception("Error")):
                result = await service.get_torrents_by_category("radarr")
                assert result == []

    @pytest.mark.asyncio
    async def test_delete_torrent_success(self):
        """Test successful torrent deletion."""
        with patch("app.arbitratarr.config.get_settings") as mock_get_settings:
            mock_settings = MagicMock()
            mock_settings.qbittorrent_url = "http://localhost:8080"
            mock_settings.qbittorrent_username = "admin"
            mock_settings.qbittorrent_password = "admin123"
            mock_get_settings.return_value = mock_settings

            service = QbittorrentService()
            mock_client = MagicMock()
            service._client = mock_client

            with patch("asyncio.to_thread", AsyncMock()):
                result = await service.delete_torrent("abc123")
                assert result is True

    @pytest.mark.asyncio
    async def test_delete_torrent_error(self):
        """Test torrent deletion with error."""
        with patch("app.arbitratarr.config.get_settings") as mock_get_settings:
            mock_settings = MagicMock()
            mock_settings.qbittorrent_url = "http://localhost:8080"
            mock_settings.qbittorrent_username = "admin"
            mock_settings.qbittorrent_password = "admin123"
            mock_get_settings.return_value = mock_settings

            service = QbittorrentService()
            mock_client = MagicMock()
            service._client = mock_client

            with patch("asyncio.to_thread", side_effect=Exception("Error")):
                result = await service.delete_torrent("abc123")
                assert result is False

    @pytest.mark.asyncio
    async def test_delete_torrent_with_files(self):
        """Test deleting torrent with files."""
        with patch("app.arbitratarr.config.get_settings") as mock_get_settings:
            mock_settings = MagicMock()
            mock_settings.qbittorrent_url = "http://localhost:8080"
            mock_settings.qbittorrent_username = "admin"
            mock_settings.qbittorrent_password = "admin123"
            mock_get_settings.return_value = mock_settings

            service = QbittorrentService()
            mock_client = MagicMock()
            service._client = mock_client

            with patch("asyncio.to_thread", AsyncMock()):
                result = await service.delete_torrent("abc123", delete_files=True)
                assert result is True