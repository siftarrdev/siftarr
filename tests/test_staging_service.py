"""Tests for StagingService."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.siftarr.models.request import MediaType, Request
from app.siftarr.models.staged_torrent import StagedTorrent
from app.siftarr.services.prowlarr_service import ProwlarrRelease
from app.siftarr.services.staging_service import StagingService


class TestStagingServiceUnit:
    """Unit tests for StagingService."""

    def test_sanitize_filename_special_chars(self):
        """Test sanitizing filenames with special characters."""
        service = StagingService(None)

        assert service._sanitize_filename("Movie: Title 2019") == "Movie_Title_2019"
        assert service._sanitize_filename("Movie/Title") == "Movie_Title"
        assert service._sanitize_filename("Movie*Title?2019") == "Movie_Title_2019"
        assert service._sanitize_filename('Movie"Title"2019') == "Movie_Title_2019"
        assert service._sanitize_filename("Movie|Title") == "Movie_Title"

    def test_sanitize_filename_spaces(self):
        """Test sanitizing filenames with spaces."""
        service = StagingService(None)

        assert service._sanitize_filename("Movie  Title   2019") == "Movie_Title_2019"

    def test_sanitize_filename_truncation(self):
        """Test that long filenames are truncated."""
        service = StagingService(None)

        long_title = "A" * 200
        result = service._sanitize_filename(long_title)
        assert len(result) == 100

    def test_generate_filename_with_group(self):
        """Test filename generation with release group."""
        service = StagingService(None)

        result = service._generate_filename(
            title="My Movie 2019",
            release_group="RARBG",
            request_id=123,
        )

        assert "My_Movie_2019" in result
        assert "RARBG" in result
        assert "123" in result

    def test_generate_filename_without_group(self):
        """Test filename generation without release group."""
        service = StagingService(None)

        result = service._generate_filename(
            title="My Movie 2019",
            release_group=None,
            request_id=123,
        )

        assert "My_Movie_2019" in result
        assert "123" in result

    def test_is_staging_enabled(self):
        """Test staging enabled check."""
        result = StagingService.is_staging_enabled(MagicMock())
        assert result is False


class TestStagingServiceIntegration:
    """Integration tests for StagingService."""

    @pytest.fixture
    def mock_db(self):
        """Create a mock database session."""
        db = AsyncMock()
        db.add = MagicMock()
        return db

    @pytest.fixture
    def service(self, mock_db):
        """Create a StagingService instance."""
        return StagingService(mock_db)

    @pytest.mark.asyncio
    async def test_save_release_no_db(self):
        """Test saving release without database session."""
        service = StagingService(None)

        mock_request = MagicMock(spec=Request)
        mock_release = MagicMock(spec=ProwlarrRelease)

        with pytest.raises(RuntimeError, match="Database session is required"):
            await service.save_release(mock_release, mock_request)

    @pytest.mark.asyncio
    async def test_get_staged_torrent_no_db(self):
        """Test getting staged torrent without database session."""
        service = StagingService(None)

        with pytest.raises(RuntimeError, match="Database session is required"):
            await service.get_staged_torrent(1)

    @pytest.mark.asyncio
    async def test_get_all_staged_no_db(self):
        """Test getting all staged torrents without database session."""
        service = StagingService(None)

        with pytest.raises(RuntimeError, match="Database session is required"):
            await service.get_all_staged()

    @pytest.mark.asyncio
    async def test_scan_staging_directory_no_db(self):
        """Test scanning staging directory without database session."""
        service = StagingService(None)

        with pytest.raises(RuntimeError, match="Database session is required"):
            await service.scan_staging_directory()

    @pytest.mark.asyncio
    async def test_delete_staged_files_success(self, service, tmp_path):
        """Test successful deletion of staged files."""
        torrent_path = tmp_path / "test.torrent"
        torrent_path.write_bytes(b"d8:announce0:")
        json_path = tmp_path / "test.json"
        json_path.write_bytes(b'{"test": true}')

        mock_staged = MagicMock(spec=StagedTorrent)
        mock_staged.torrent_path = str(torrent_path)
        mock_staged.json_path = str(json_path)

        result = await service.delete_staged_files(mock_staged)

        assert result is True
        assert not torrent_path.exists()
        assert not json_path.exists()

    @pytest.mark.asyncio
    async def test_delete_staged_files_missing_files(self, service, tmp_path):
        """Test deletion when files are already missing."""
        mock_staged = MagicMock(spec=StagedTorrent)
        mock_staged.torrent_path = str(tmp_path / "nonexistent.torrent")
        mock_staged.json_path = str(tmp_path / "nonexistent.json")

        result = await service.delete_staged_files(mock_staged)

        assert result is True

    @pytest.mark.asyncio
    async def test_delete_staged_files_os_error(self, service, tmp_path):
        """Test deletion with OS error."""
        mock_staged = MagicMock(spec=StagedTorrent)
        mock_staged.torrent_path = str(tmp_path / "test.torrent")
        mock_staged.json_path = str(tmp_path / "test.json")

        with (
            patch("os.path.exists", return_value=True),
            patch("os.remove", side_effect=OSError("Permission denied")),
        ):
            result = await service.delete_staged_files(mock_staged)

        assert result is False

    @pytest.mark.asyncio
    async def test_save_release_persists_selection_source(self, service, tmp_path):
        """Saved staged torrents should retain whether selection was rule or manual."""
        request = MagicMock(spec=Request)
        request.id = 42
        request.external_id = "ext-42"
        request.media_type = MediaType.MOVIE
        request.tmdb_id = 123
        request.tvdb_id = None
        request.title = "Example Movie"
        request.year = 2024

        release = ProwlarrRelease(
            title="Example.Movie.2024.1080p",
            size=1_500_000_000,
            seeders=10,
            leechers=2,
            download_url="magnet:?xt=urn:btih:example",
            magnet_url="magnet:?xt=urn:btih:example",
            indexer="Indexer A",
        )

        with patch("app.siftarr.services.staging_service.STAGING_DIR", tmp_path):
            saved = await service.save_release(
                release,
                request,
                score=15,
                selection_source="manual",
            )

        added_record = service.db.add.call_args.args[0]
        assert added_record.selection_source == "manual"
        assert saved is service.db.refresh.await_args_list[0].args[0]
