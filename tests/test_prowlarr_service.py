"""Tests for ProwlarrService."""

import pytest

from app.siftarr.services.prowlarr_service import ProwlarrSearchResult, ProwlarrService


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

    def test_extract_release_items_supports_flat_search_results(self) -> None:
        """Flat Prowlarr search payloads should still be parsed as releases."""
        payload = [
            {
                "title": "Return.to.Me.2000.1080p.x265-GROUP",
                "downloadUrl": "https://example.com/return-to-me.torrent",
                "indexer": "IPT",
            }
        ]

        releases = ProwlarrService._extract_release_items(payload)

        assert len(releases) == 1
        assert releases[0]["title"] == "Return.to.Me.2000.1080p.x265-GROUP"

    def test_extract_release_items_supports_nested_search_results(self) -> None:
        """Nested Prowlarr search payloads should still be parsed as releases."""
        payload = [
            {
                "indexer": "IPT",
                "releases": [
                    {
                        "title": "Return.to.Me.2000.1080p.x264-GROUP",
                        "downloadUrl": "https://example.com/return-to-me.torrent",
                    }
                ],
            }
        ]

        releases = ProwlarrService._extract_release_items(payload)

        assert len(releases) == 1
        assert releases[0]["title"] == "Return.to.Me.2000.1080p.x264-GROUP"

    def test_build_movie_query_uses_tmdbid_tokens(self) -> None:
        """Movie queries should encode metadata in the query string."""
        query = ProwlarrService._build_movie_query("Return to Me", 1234, 2000)

        assert query == "Return to Me {tmdbid:1234} {year:2000}"

    def test_build_tv_query_uses_tvsearch_tokens(self) -> None:
        """TV queries should encode metadata in the query string."""
        query = ProwlarrService._build_tv_query(
            "Example Show", 5678, season=1, episode=2, year=2024
        )

        assert query == "Example Show {tvdbid:5678} {season:1} {episode:2} {year:2024}"

    def test_build_tv_query_handles_season_only_searches(self) -> None:
        """TV queries should still work when only a season is requested."""
        query = ProwlarrService._build_tv_query("Example Show", 5678, season=8, year=2024)

        assert query == "Example Show {tvdbid:5678} {season:8} {year:2024}"

    def test_build_tv_title_query_handles_season_only_searches(self) -> None:
        """Fallback TV queries should still work when only a season is requested."""
        query = ProwlarrService._build_tv_title_query("Example Show", season=8, year=2024)

        assert query == "Example Show S08 2024"

    @pytest.mark.asyncio
    async def test_search_by_tmdbid_falls_back_to_title_query(self, monkeypatch) -> None:
        """Movie search should retry with a title query when metadata search is empty."""
        service = ProwlarrService()
        calls = []

        async def fake_search(params):
            calls.append(params)
            if len(calls) == 1:
                return ProwlarrSearchResult(releases=[], query_time_ms=10)
            return ProwlarrSearchResult(releases=[], query_time_ms=15)

        monkeypatch.setattr(service, "_search", fake_search)

        await service.search_by_tmdbid(2621, title="Return to Me", year=2000)

        assert calls[0]["type"] == "movie"
        assert calls[0]["query"] == "Return to Me {tmdbid:2621} {year:2000}"
        assert calls[1]["type"] == "search"
        assert calls[1]["query"] == "Return to Me 2000"

    @pytest.mark.asyncio
    async def test_search_by_tvdbid_falls_back_to_title_query(self, monkeypatch) -> None:
        """TV search should retry with a title query when metadata search is empty."""
        service = ProwlarrService()
        calls = []

        async def fake_search(params):
            calls.append(params)
            if len(calls) == 1:
                return ProwlarrSearchResult(releases=[], query_time_ms=10)
            return ProwlarrSearchResult(releases=[], query_time_ms=15)

        monkeypatch.setattr(service, "_search", fake_search)

        await service.search_by_tvdbid(5678, title="Example Show", season=1, episode=2, year=2024)

        assert calls[0]["type"] == "tvsearch"
        assert calls[0]["query"] == "Example Show {tvdbid:5678} {season:1} {episode:2} {year:2024}"
        assert calls[1]["type"] == "search"
        assert calls[1]["query"] == "Example Show S01E02 2024"
