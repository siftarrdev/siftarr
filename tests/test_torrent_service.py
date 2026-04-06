"""Tests for TorrentService."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, mock_open, patch

import pytest

from app.arbitratarr.services.torrent_service import TorrentService


class TestTorrentService:
    """Test cases for TorrentService."""

    @pytest.mark.asyncio
    async def test_download_torrent_success(self):
        """Test successful torrent download."""
        torrent_content = b"d8:announce0:7d20ed263cd6e3f7c6df7a72a6e153ac5e4ab604e"

        mock_response = MagicMock()
        mock_response.content = torrent_content
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client_class.return_value = mock_client

            with patch("builtins.open", mock_open()) as mock_file:
                save_path = Path("/tmp/test.torrent")
                result = await TorrentService.download_torrent("http://example.com/test.torrent", save_path)

                assert result is True
                mock_file.assert_called_with(save_path, "wb")

    @pytest.mark.asyncio
    async def test_download_torrent_invalid_url(self):
        """Test download with invalid URL."""
        save_path = Path("/tmp/test.torrent")
        result = await TorrentService.download_torrent("not-a-url", save_path)

        assert result is False

    @pytest.mark.asyncio
    async def test_download_torrent_invalid_content(self):
        """Test download with non-torrent content."""
        mock_response = MagicMock()
        mock_response.content = b"not a torrent file"
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client_class.return_value = mock_client

            save_path = Path("/tmp/test.torrent")
            result = await TorrentService.download_torrent("http://example.com/test.torrent", save_path)

            assert result is False

    @pytest.mark.asyncio
    async def test_download_torrent_request_error(self):
        """Test download with request error."""
        import httpx

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(side_effect=httpx.RequestError("Network error"))
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client_class.return_value = mock_client

            save_path = Path("/tmp/test.torrent")
            result = await TorrentService.download_torrent("http://example.com/test.torrent", save_path)

            assert result is False

    @pytest.mark.asyncio
    async def test_download_torrent_http_error(self):
        """Test download with HTTP error."""
        import httpx

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(side_effect=httpx.HTTPStatusError("Error", request=MagicMock(), response=MagicMock()))
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client_class.return_value = mock_client

            save_path = Path("/tmp/test.torrent")
            result = await TorrentService.download_torrent("http://example.com/test.torrent", save_path)

            assert result is False

    def test_validate_torrent_file_valid(self, tmp_path):
        """Test validating a valid torrent file."""
        torrent_file = tmp_path / "test.torrent"
        torrent_file.write_bytes(b"d8:announce0:7d20ed263cd6e3f7c6df7a72a6e153ac5e4ab604e")

        result = TorrentService.validate_torrent_file(torrent_file)

        assert result is True

    def test_validate_torrent_file_invalid(self, tmp_path):
        """Test validating an invalid torrent file."""
        invalid_file = tmp_path / "test.txt"
        invalid_file.write_bytes(b"not a torrent file")

        result = TorrentService.validate_torrent_file(invalid_file)

        assert result is False

    def test_validate_torrent_file_missing(self, tmp_path):
        """Test validating a missing file."""
        missing_file = tmp_path / "nonexistent.torrent"

        result = TorrentService.validate_torrent_file(missing_file)

        assert result is False

    def test_validate_torrent_file_short_header(self, tmp_path):
        """Test validating a file with short header."""
        short_file = tmp_path / "test.torrent"
        short_file.write_bytes(b"d")

        result = TorrentService.validate_torrent_file(short_file)

        assert result is False

    def test_validate_torrent_file_empty(self, tmp_path):
        """Test validating an empty file."""
        empty_file = tmp_path / "test.torrent"
        empty_file.write_bytes(b"")

        result = TorrentService.validate_torrent_file(empty_file)

        assert result is False