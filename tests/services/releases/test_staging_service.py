"""Tests for StagingService."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.siftarr.models.release import Release
from app.siftarr.models.request import MediaType, Request
from app.siftarr.models.staged_torrent import StagedTorrent
from app.siftarr.services.integrations.prowlarr_service import ProwlarrRelease
from app.siftarr.services.releases.staging_service import StagingService


class TestStagingServiceUnit:
    """Unit tests for StagingService."""

    @pytest.fixture
    def mock_db(self):
        """Create a mock database session."""
        return MagicMock()

    def test_sanitize_filename_special_chars(self, mock_db):
        """Test sanitizing filenames with special characters."""
        service = StagingService(mock_db)

        assert service._sanitize_filename("Movie: Title 2019") == "Movie_Title_2019"
        assert service._sanitize_filename("Movie/Title") == "Movie_Title"
        assert service._sanitize_filename("Movie*Title?2019") == "Movie_Title_2019"
        assert service._sanitize_filename('Movie"Title"2019') == "Movie_Title_2019"
        assert service._sanitize_filename("Movie|Title") == "Movie_Title"

    def test_sanitize_filename_spaces(self, mock_db):
        """Test sanitizing filenames with spaces."""
        service = StagingService(mock_db)

        assert service._sanitize_filename("Movie  Title   2019") == "Movie_Title_2019"

    def test_sanitize_filename_truncation(self, mock_db):
        """Test that long filenames are truncated."""
        service = StagingService(mock_db)

        long_title = "A" * 200
        result = service._sanitize_filename(long_title)
        assert len(result) == 100

    def test_generate_filename_with_group(self, mock_db):
        """Test filename generation with release group."""
        service = StagingService(mock_db)

        result = service._generate_filename(
            title="My Movie 2019",
            release_group="RARBG",
            request_id=123,
        )

        assert "My_Movie_2019" in result
        assert "RARBG" in result
        assert "123" in result

    def test_generate_filename_without_group(self, mock_db):
        """Test filename generation without release group."""
        service = StagingService(mock_db)

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

    @pytest.mark.asyncio
    async def test_rule_selection_rejects_zero_seeder_release(self, mock_db):
        """Automatic selections must never hand off torrents with no seeders."""
        request = MagicMock(spec=Request, id=42)
        release = MagicMock(spec=Release, title="Dead.Torrent", seeders=0)

        with pytest.raises(RuntimeError, match="No stored releases"):
            await StagingService(mock_db).use_releases(
                request,
                [release],
                selection_source="rule",
            )


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

        with patch("app.siftarr.services.releases.staging_service.STAGING_DIR", tmp_path):
            saved = await service.save_release(
                release,
                request,
                score=15,
                selection_source="manual",
            )

        added_record = service.db.add.call_args.args[0]
        assert added_record.selection_source == "manual"
        assert saved is service.db.refresh.await_args_list[0].args[0]

    @pytest.mark.asyncio
    async def test_save_release_skips_eager_download_and_stores_sources(self, service, tmp_path):
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
            download_url="https://example.test/example.torrent",
            magnet_url="magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567",
            indexer="Indexer A",
        )

        with (
            patch("app.siftarr.services.releases.staging_service.STAGING_DIR", tmp_path),
            patch("app.siftarr.services.releases.staging_service.get_shared_client") as get_client,
        ):
            await service.save_release(release, request)

        get_client.assert_not_called()
        assert not list(tmp_path.glob("*.torrent"))
        metadata = json.loads(next(tmp_path.glob("*.json")).read_text())
        assert metadata["release"]["download_url"] == release.download_url
        assert metadata["release"]["magnet_url"] == release.magnet_url

    @pytest.mark.asyncio
    async def test_save_release_can_batch_without_commit(self, service, tmp_path):
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
            download_url="https://example.test/example.torrent",
            magnet_url=None,
            indexer="Indexer A",
        )

        with patch("app.siftarr.services.releases.staging_service.STAGING_DIR", tmp_path):
            await service.save_release(release, request, commit=False)

        service.db.flush.assert_awaited_once()
        service.db.commit.assert_not_awaited()
        service.db.refresh.assert_not_awaited()
