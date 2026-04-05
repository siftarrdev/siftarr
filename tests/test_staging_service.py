"""Tests for StagingService."""

from app.arbitratarr.services.staging_service import StagingService


class TestStagingService:
    """Test cases for StagingService."""

    def test_sanitize_filename(self) -> None:
        """Test filename sanitization."""
        service = StagingService(None)

        assert service._sanitize_filename("Movie: Title 2019") == "Movie_Title_2019"
        assert service._sanitize_filename("Movie/Title") == "Movie_Title"
        assert service._sanitize_filename("Movie  Title   2019") == "Movie_Title_2019"

    def test_generate_filename(self) -> None:
        """Test filename generation."""
        service = StagingService(None)

        result = service._generate_filename(
            title="My Movie 2019",
            release_group="RARBG",
            request_id=123,
        )

        assert "My_Movie_2019" in result
        assert "RARBG" in result
        assert "123" in result

    def test_generate_filename_no_group(self) -> None:
        """Test filename generation without release group."""
        service = StagingService(None)

        result = service._generate_filename(
            title="My Movie 2019",
            release_group=None,
            request_id=123,
        )

        assert "My_Movie_2019" in result
        assert "123" in result
