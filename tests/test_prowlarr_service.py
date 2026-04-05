"""Tests for ProwlarrService."""

from app.arbitratarr.services.prowlarr_service import ProwlarrService


class TestProwlarrService:
    """Test cases for ProwlarrService."""

    def test_extract_resolution(self) -> None:
        """Test resolution extraction from title."""
        service = ProwlarrService()

        assert service._extract_resolution("Movie.2024.2160p.x264") == "2160p"
        assert service._extract_resolution("Movie.2024.1080p.x264") == "1080p"
        assert service._extract_resolution("Movie.2024.720p.x264") == "720p"
        assert service._extract_resolution("Movie.2024.480p.x264") == "480p"
        assert service._extract_resolution("Movie.2024.x264") is None

    def test_extract_codec(self) -> None:
        """Test codec extraction from title."""
        service = ProwlarrService()

        assert service._extract_codec("Movie.2024.1080p.x265-RLSGRP") == "x265"
        assert service._extract_codec("Movie.2024.1080p.H.265-RLSGRP") == "x265"
        assert service._extract_codec("Movie.2024.1080p.HEVC-RLSGRP") == "x265"
        assert service._extract_codec("Movie.2024.1080p.x264-RLSGRP") == "x264"
        assert service._extract_codec("Movie.2024.1080p.AV1-RLSGRP") == "AV1"

    def test_extract_release_group(self) -> None:
        """Test release group extraction."""
        service = ProwlarrService()

        # This test depends on the regex pattern
        assert service._extract_release_group("Movie.2024.1080p-RLSGRP") is not None

    def test_parse_date(self) -> None:
        """Test date parsing."""
        service = ProwlarrService()

        date_str = "2024-01-15T10:30:00Z"
        result = service._parse_date(date_str)
        assert result is not None
        assert result.year == 2024
        assert result.month == 1
        assert result.day == 15
