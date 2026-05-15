"""Tests for ProwlarrService."""

import pytest

from app.siftarr.config import Settings
from app.siftarr.services.integrations.prowlarr_service import (
    ProwlarrRelease,
    ProwlarrSearchResult,
    ProwlarrService,
)


def _release(index: int) -> ProwlarrRelease:
    return ProwlarrRelease(
        title=f"The Rookie S01E{index:02d}",
        size=1000,
        seeders=1,
        leechers=0,
        download_url=f"http://example.com/{index}",
        indexer="IPTorrents",
    )


class TestProwlarrService:
    """Test cases for ProwlarrService."""

    def test_normalize_search_title_strips_apostrophes(self) -> None:
        """Apostrophes should be stripped for broader indexer matching."""
        assert ProwlarrService._normalize_search_title("Margo's") == "Margos"
        assert ProwlarrService._normalize_search_title("Tom & Jerry's") == "Tom & Jerrys"

    def test_normalize_search_title_preserves_normal_titles(self) -> None:
        """Titles without apostrophes should pass through unchanged, and whitespace stripped."""
        assert ProwlarrService._normalize_search_title("The Mentalist") == "The Mentalist"
        assert ProwlarrService._normalize_search_title("  Return to Me  ") == "Return to Me"

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
        assert service._extract_codec("Movie.2024.1080p.H 265-RLSGRP") == "x265"
        assert service._extract_codec("Movie.2024.1080p.HEVC-RLSGRP") == "x265"
        assert service._extract_codec("Movie.2024.1080p.x264-RLSGRP") == "x264"
        assert service._extract_codec("Movie.2024.1080p.H 264-RLSGRP") == "x264"
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

    def test_parse_release_info_supports_alternate_file_count_fields(self) -> None:
        """Movie releases should preserve file counts from alternate Prowlarr fields."""
        service = ProwlarrService()

        release = service._parse_release_info(
            {
                "title": "What.a.Girl.Wants.2003.1080p.WEBRip.x264",
                "size": 123,
                "seeders": 10,
                "leechers": 1,
                "downloadUrl": "https://example.com/torrent",
                "indexer": "Test",
                "fileCount": 1,
            }
        )

        assert release.files == 1

    def test_parse_release_info_prefers_api_release_group(self) -> None:
        """_parse_release_info should prefer the API's releaseGroup field over title parsing."""
        service = ProwlarrService()

        release = service._parse_release_info(
            {
                "title": "Movie.2024.1080p.x264",  # no group in title
                "releaseGroup": "MeGusta",
                "size": 1234,
                "seeders": 10,
                "leechers": 1,
                "downloadUrl": "https://example.com/torrent",
                "indexer": "Test",
            }
        )

        assert release.release_group == "MeGusta"

    def test_parse_release_info_falls_back_to_title_parsing(self) -> None:
        """_parse_release_info should fall back to parsing release group from title."""
        service = ProwlarrService()

        release = service._parse_release_info(
            {
                "title": "Movie.2024.1080p.x264-MeGusta",
                "size": 1234,
                "seeders": 10,
                "leechers": 1,
                "downloadUrl": "https://example.com/torrent",
                "indexer": "Test",
            }
        )

        assert release.release_group == "MeGusta"

    def test_parse_release_info_reads_uploaded_by(self) -> None:
        """_parse_release_info should read the uploadedBy API field."""
        service = ProwlarrService()

        release = service._parse_release_info(
            {
                "title": "Movie.2024.1080p.x264-RLSGRP",
                "uploadedBy": "SomeUploader",
                "size": 1234,
                "seeders": 10,
                "leechers": 1,
                "downloadUrl": "https://example.com/torrent",
                "indexer": "Test",
            }
        )

        assert release.uploaded_by == "SomeUploader"

    def test_parse_release_info_falls_back_to_uploader_field(self) -> None:
        """_parse_release_info should fall back to the uploader API field."""
        service = ProwlarrService()

        release = service._parse_release_info(
            {
                "title": "Movie.2024.1080p.x264-RLSGRP",
                "uploader": "FallbackUploader",
                "size": 1234,
                "seeders": 10,
                "leechers": 1,
                "downloadUrl": "https://example.com/torrent",
                "indexer": "Test",
            }
        )

        assert release.uploaded_by == "FallbackUploader"

    def test_parse_release_info_uploaded_by_none_when_missing(self) -> None:
        """_parse_release_info should set uploaded_by to None when not in API response."""
        service = ProwlarrService()

        release = service._parse_release_info(
            {
                "title": "Movie.2024.1080p.x264-RLSGRP",
                "size": 1234,
                "seeders": 10,
                "leechers": 1,
                "downloadUrl": "https://example.com/torrent",
                "indexer": "Test",
            }
        )

        assert release.uploaded_by is None

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

    def test_build_tv_query_handles_broad_pack_searches(self) -> None:
        """TV queries should support broad searches without season or episode tokens."""
        query = ProwlarrService._build_tv_query("Example Show", 5678, year=2024)

        assert query == "Example Show {tvdbid:5678} {year:2024}"

    def test_build_tv_title_query_handles_season_only_searches(self) -> None:
        """Fallback TV queries should still work when only a season is requested."""
        query = ProwlarrService._build_tv_title_query("Example Show", season=8, year=2024)

        assert query == "Example Show S08 2024"

    def test_build_tv_season_strategy_queries_for_iptorrents(self) -> None:
        """Season strategies should produce IPTorrents-compatible query params."""
        service = ProwlarrService(Settings(prowlarr_tv_strategy_tvdb_enabled=True))

        strategies = service._tv_season_strategy_queries(
            "The Rookie", 1, imdbid="tt7587890", tvdbid=350665
        )

        assert strategies == [
            ("title_sxx", "search", "The Rookie S01"),
            ("imdb_season", "tvsearch", "The Rookie {imdbid:7587890} {season:1}"),
            ("title_season_token", "tvsearch", "The Rookie {season:1}"),
            ("tvdb_season", "tvsearch", "The Rookie {tvdbid:350665} {season:1}"),
        ]

    def test_build_tv_season_strategy_queries_skip_unavailable_optional_metadata(self) -> None:
        service = ProwlarrService(Settings(prowlarr_tv_strategy_tvdb_enabled=True))

        strategies = service._tv_season_strategy_queries("The Rookie", 1)

        assert [strategy[0] for strategy in strategies] == [
            "title_sxx",
            "title_season_token",
        ]

    @pytest.mark.asyncio
    async def test_search_by_tmdbid_falls_back_to_title_query(self, monkeypatch) -> None:
        """Movie search should retry with a title query when metadata search is empty."""
        service = ProwlarrService()
        calls = []

        async def fake_search(params, **kwargs):
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

        async def fake_search(params, **kwargs):
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

    @pytest.mark.asyncio
    async def test_search_by_tvdbid_broad_search_tries_multiple_queries(self, monkeypatch) -> None:
        """Broad TV search (no season, no episode) should try multiple title query strategies."""
        service = ProwlarrService()
        calls = []

        async def fake_search(params, **kwargs):
            calls.append(params)
            return ProwlarrSearchResult(releases=[], query_time_ms=10)

        monkeypatch.setattr(service, "_search", fake_search)

        await service.search_by_tvdbid(5678, title="The Mentalist", year=2024)

        # First call: metadata query
        assert calls[0]["type"] == "tvsearch"
        assert calls[0]["query"] == "The Mentalist {tvdbid:5678} {year:2024}"
        # Subsequent calls: multiple title query strategies
        assert calls[1]["type"] == "search"
        assert calls[1]["query"] == "The Mentalist S01-"
        assert calls[2]["type"] == "search"
        assert calls[2]["query"] == "The Mentalist complete"
        assert calls[3]["type"] == "search"
        assert calls[3]["query"] == "The Mentalist season 1-"

    @pytest.mark.asyncio
    async def test_search_by_tvdbid_broad_search_aggregates_unique_releases(
        self, monkeypatch
    ) -> None:
        """Broad TV search should return all unique releases across query strategies."""
        service = ProwlarrService()
        call_count = [0]

        def make_release(index: int, title: str) -> ProwlarrRelease:
            return ProwlarrRelease(
                title=title,
                size=1000,
                seeders=1,
                leechers=0,
                download_url=f"http://example.com/{index}",
                magnet_url=None,
                indexer="test",
            )

        async def fake_search(params, **kwargs):
            call_count[0] += 1
            query = params.get("query", "")
            if "S01-" in query:
                return ProwlarrSearchResult(
                    releases=[make_release(1, "Show S01-S03")], query_time_ms=10
                )
            elif "complete" in query:
                return ProwlarrSearchResult(
                    releases=[make_release(2, "Show Complete")], query_time_ms=10
                )
            elif "season 1-" in query:
                return ProwlarrSearchResult(
                    releases=[make_release(3, "Show Season 1-5")], query_time_ms=10
                )
            return ProwlarrSearchResult(releases=[], query_time_ms=10)

        monkeypatch.setattr(service, "_search", fake_search)

        result = await service.search_by_tvdbid(5678, title="The Mentalist", year=2024)

        assert len(result.releases) == 3
        assert result.query_time_ms == 40  # 10ms * 4 queries

    @pytest.mark.asyncio
    async def test_search_by_tvdbid_broad_search_deduplicates_by_url(self, monkeypatch) -> None:
        """Broad TV search should deduplicate releases with the same download URL."""
        service = ProwlarrService()

        shared_release = ProwlarrRelease(
            title="Show S01-S03",
            size=1000,
            seeders=1,
            leechers=0,
            download_url="http://example.com/same",
            magnet_url=None,
            indexer="test",
        )

        async def fake_search(params, **kwargs):
            # All queries return the same release (same URL)
            return ProwlarrSearchResult(releases=[shared_release], query_time_ms=10)

        monkeypatch.setattr(service, "_search", fake_search)

        result = await service.search_by_tvdbid(5678, title="The Mentalist", year=2024)

        # Should only have 1 release despite multiple queries returning same URL
        assert len(result.releases) == 1
        assert result.releases[0].download_url == "http://example.com/same"

    @pytest.mark.asyncio
    async def test_search_tv_season_page_sets_offset_without_limit(self, monkeypatch) -> None:
        service = ProwlarrService(Settings(prowlarr_tv_page_size=100))
        calls = []

        async def fake_search(params, **kwargs):
            calls.append(params)
            return ProwlarrSearchResult(releases=[_release(1)], query_time_ms=10)

        monkeypatch.setattr(service, "_search", fake_search)

        result = await service.search_tv_season_page("The Rookie", 1, "title_sxx", offset=100)

        assert calls == [
            {
                "type": "search",
                "query": "The Rookie S01",
                "categories": [5000],
                "offset": 100,
            }
        ]
        assert "limit" not in calls[0]
        assert result.offset == 100
        assert result.page_size == 100
        assert result.page_count == 1
        assert result.is_short_page is True
        assert result.query_strategy == "title_sxx"

    @pytest.mark.asyncio
    async def test_search_tv_season_sweep_increments_offsets_and_stops_on_short_page(
        self, monkeypatch
    ) -> None:
        service = ProwlarrService(
            Settings(
                prowlarr_tv_page_size=100,
                prowlarr_tv_strategy_imdb_enabled=False,
                prowlarr_tv_strategy_title_season_token_enabled=False,
            )
        )
        calls = []

        async def fake_search(params, **kwargs):
            calls.append(params)
            count = 100 if params["offset"] in {0, 100} else 54
            return ProwlarrSearchResult(
                releases=[_release(params["offset"] + i) for i in range(count)],
                query_time_ms=10,
            )

        monkeypatch.setattr(service, "_search", fake_search)

        result = await service.search_tv_season_sweep("The Rookie", 1)

        assert [call["offset"] for call in calls] == [0, 100, 200]
        assert all("limit" not in call for call in calls)
        assert len(result.releases) == 254

    @pytest.mark.asyncio
    async def test_search_tv_season_sweep_stops_on_empty_page(self, monkeypatch) -> None:
        service = ProwlarrService(
            Settings(
                prowlarr_tv_page_size=100,
                prowlarr_tv_strategy_imdb_enabled=False,
                prowlarr_tv_strategy_title_season_token_enabled=False,
            )
        )
        calls = []

        async def fake_search(params, **kwargs):
            calls.append(params)
            releases = [_release(i) for i in range(100)] if params["offset"] == 0 else []
            return ProwlarrSearchResult(releases=releases, query_time_ms=10)

        monkeypatch.setattr(service, "_search", fake_search)

        result = await service.search_tv_season_sweep("The Rookie", 1)

        assert [call["offset"] for call in calls] == [0, 100]
        assert len(result.releases) == 100

    @pytest.mark.asyncio
    async def test_search_tv_season_sweep_continues_past_old_max_pages(self, monkeypatch) -> None:
        service = ProwlarrService(
            Settings(
                prowlarr_tv_page_size=100,
                prowlarr_tv_strategy_imdb_enabled=False,
                prowlarr_tv_strategy_title_season_token_enabled=False,
            )
        )
        calls = []

        async def fake_search(params, **kwargs):
            calls.append(params)
            if params["offset"] == 300:
                return ProwlarrSearchResult(releases=[_release(300)], query_time_ms=10)
            return ProwlarrSearchResult(
                releases=[_release(i) for i in range(100)], query_time_ms=10
            )

        monkeypatch.setattr(service, "_search", fake_search)

        result = await service.search_tv_season_sweep("The Rookie", 1)

        assert [call["offset"] for call in calls] == [0, 100, 200, 300]
        assert len(result.releases) == 301

    @pytest.mark.asyncio
    async def test_search_tv_season_sweep_keeps_all_results_past_old_max_results(
        self, monkeypatch
    ) -> None:
        service = ProwlarrService(
            Settings(
                prowlarr_tv_page_size=100,
                prowlarr_tv_strategy_imdb_enabled=False,
                prowlarr_tv_strategy_title_season_token_enabled=False,
            )
        )
        calls = []

        async def fake_search(params, **kwargs):
            calls.append(params)
            if params["offset"] == 200:
                return ProwlarrSearchResult(releases=[_release(200)], query_time_ms=10)
            return ProwlarrSearchResult(
                releases=[_release(i) for i in range(100)], query_time_ms=10
            )

        monkeypatch.setattr(service, "_search", fake_search)

        result = await service.search_tv_season_sweep("The Rookie", 1)

        assert [call["offset"] for call in calls] == [0, 100, 200]
        assert len(result.releases) == 201

    @pytest.mark.asyncio
    async def test_search_tv_season_sweep_keeps_title_and_imdb_when_tvdb_empty(
        self, monkeypatch
    ) -> None:
        """IPTorrents can return 0 for TVDB while title/IMDb strategies succeed."""
        service = ProwlarrService(
            Settings(
                prowlarr_tv_page_size=100,
                prowlarr_tv_strategy_title_season_token_enabled=False,
                prowlarr_tv_strategy_tvdb_enabled=True,
            )
        )
        calls = []

        async def fake_search(params, **kwargs):
            calls.append(params)
            query = params["query"]
            if "{tvdbid:" in query:
                return ProwlarrSearchResult(releases=[], query_time_ms=10)
            return ProwlarrSearchResult(releases=[_release(len(calls))], query_time_ms=10)

        monkeypatch.setattr(service, "_search", fake_search)

        result = await service.search_tv_season_sweep(
            "The Rookie", 1, imdbid="tt7587890", tvdbid=350665
        )

        assert [call["query"] for call in calls] == [
            "The Rookie S01",
            "The Rookie {imdbid:7587890} {season:1}",
            "The Rookie {tvdbid:350665} {season:1}",
        ]
        assert len(result.releases) == 2
