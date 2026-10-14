"""Tests for ProwlarrService."""

import json
from unittest.mock import AsyncMock

import httpx
import pytest

from app.siftarr.config import Settings
from app.siftarr.services.integrations.prowlarr_service import (
    ProwlarrRelease,
    ProwlarrSearchResult,
    ProwlarrService,
    _cache_get,
    _cache_set,
    _search_cache,
)


def _release(index: int, title: str | None = None, url: str | None = None) -> ProwlarrRelease:
    return ProwlarrRelease(
        title=title or f"The Rookie S01E{index:02d}",
        size=1000,
        seeders=1,
        leechers=0,
        download_url=url or f"http://example.com/{index}",
        indexer="IPTorrents",
    )


def _result(*releases: ProwlarrRelease, ms: int = 10) -> ProwlarrSearchResult:
    return ProwlarrSearchResult(releases=list(releases), query_time_ms=ms)


@pytest.fixture
def search_calls(monkeypatch):
    """Replace ProwlarrService._search; returns (calls, set_handler)."""
    calls: list[dict] = []
    handler = {"fn": lambda params: _result()}

    async def fake_search(self, params, **kwargs):
        calls.append(params)
        return handler["fn"](params)

    monkeypatch.setattr(ProwlarrService, "_search", fake_search)

    def set_handler(fn):
        handler["fn"] = fn

    return calls, set_handler


class TestSearchCache:
    @pytest.mark.parametrize(
        ("settings_kwargs", "one_cached", "two_cached"),
        [
            ({}, True, True),
            ({"prowlarr_search_cache_max_entries": 1}, False, True),
            ({"prowlarr_search_cache_ttl_seconds": 0}, False, False),
        ],
        ids=["defaults", "custom_size_evicts_oldest", "zero_ttl_expires"],
    )
    def test_settings_control_size_and_ttl(self, settings_kwargs, one_cached, two_cached):
        _search_cache.clear()
        settings = Settings(**settings_kwargs)
        first, second = _result(_release(1)), _result(_release(2))

        _cache_set("one", first, settings)
        _cache_set("two", second, settings)

        assert _cache_get("one", settings) == (first if one_cached else None)
        assert _cache_get("two", settings) == (second if two_cached else None)


class TestHttpBoundary:
    async def test_http_error_status_is_reported_in_result(self, prowlarr_service, http_stub):
        http_stub.reply(httpx.Response(500))

        result = await prowlarr_service._search({"type": "search", "query": "x"})

        assert result.error == "HTTP 500"
        assert result.releases == []
        assert http_stub.requests[0].headers["X-Api-Key"] == "key"

    async def test_failed_search_is_not_cached(self, prowlarr_service, http_stub):
        http_stub.reply(httpx.Response(503), httpx.Response(200, json=[]))

        first = await prowlarr_service._search({"type": "search", "query": "x"})
        second = await prowlarr_service._search({"type": "search", "query": "x"})

        assert first.error == "HTTP 503"
        assert second.error is None
        assert len(http_stub.requests) == 2

    async def test_malformed_json_body_propagates_decode_error(self, prowlarr_service, http_stub):
        """_search only catches httpx.RequestError, so a bad 200 body raises to the caller."""
        http_stub.reply(httpx.Response(200, content=b"<html>not json</html>"))

        with pytest.raises(json.JSONDecodeError):
            await prowlarr_service._search({"type": "search", "query": "x"})

    async def test_transport_error_is_reported_in_result(self, prowlarr_service, http_stub):
        http_stub.reply(httpx.ConnectError("refused"))

        result = await prowlarr_service._search({"type": "search", "query": "x"})

        assert result.releases == []
        assert "Request error" in (result.error or "")


class TestNormalizeSearchTitle:
    @pytest.mark.parametrize(
        ("title", "expected"),
        [
            ("Margo's", "Margos"),
            ("Tom & Jerry's", "Tom & Jerrys"),
            ("The Mentalist", "The Mentalist"),
            ("  Return to Me  ", "Return to Me"),
            *[(f"Let{mark}s", "Lets") for mark in "'`´‘’‛ʻʼʹ′＇ꞌ"],
        ],
    )
    def test_normalize(self, title, expected):
        assert ProwlarrService._normalize_search_title(title) == expected


class TestParseReleaseInfo:
    @pytest.mark.parametrize(
        ("raw", "resolution", "codec", "group", "year"),
        [
            ({"title": "Movie.2024.2160p.x264"}, "2160p", "x264", None, None),
            ({"title": "Movie.2024.1080p.x265-RLSGRP"}, "1080p", "x265", "RLSGRP", None),
            ({"title": "Movie.2024.720p.H.265-RLSGRP"}, "720p", "x265", "RLSGRP", None),
            ({"title": "Movie.2024.480p.H 265-RLSGRP"}, "480p", "x265", "RLSGRP", None),
            ({"title": "Movie.2024.1080p.HEVC-RLSGRP"}, "1080p", "x265", "RLSGRP", None),
            ({"title": "Movie.2024.1080p.H 264-RLSGRP"}, "1080p", "x264", "RLSGRP", None),
            ({"title": "Movie.2024.1080p.AV1-RLSGRP"}, "1080p", "AV1", "RLSGRP", None),
            ({"title": "Movie.2024"}, None, None, None, None),
            (
                {"title": "Movie.2024.1080p.x264", "releaseGroup": "MeGusta"},
                "1080p",
                "x264",
                "MeGusta",
                None,
            ),
            (
                {"title": "Movie.1080p.x264-TITLEGRP", "releaseGroup": "ApiGroup"},
                "1080p",
                "x264",
                "ApiGroup",
                None,
            ),
            (
                {"title": "Movie", "publishDate": "2024-01-15T10:30:00Z"},
                None,
                None,
                None,
                2024,
            ),
            ({"title": "Movie", "publishDate": "not a date"}, None, None, None, None),
        ],
        ids=[
            "2160p_x264_no_group",
            "x265",
            "dotted_h265",
            "spaced_h265",
            "hevc",
            "spaced_h264",
            "av1",
            "nothing_parsed",
            "api_group_without_title_group",
            "api_group_wins_over_title_group",
            "publish_date",
            "bad_publish_date",
        ],
    )
    def test_title_and_date_parsing(self, prowlarr_service, raw, resolution, codec, group, year):
        release = prowlarr_service._parse_release_info({"indexer": "Test", **raw})

        assert release.resolution == resolution
        assert release.codec == codec
        if group:
            assert release.release_group == group
        assert (release.publish_date.year if release.publish_date else None) == year

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ({"uploadedBy": "SomeUploader"}, "SomeUploader"),
            ({"uploader": "FallbackUploader"}, "FallbackUploader"),
            ({"uploadedBy": "Primary", "uploader": "Other"}, "Primary"),
            ({}, None),
        ],
        ids=["uploaded_by", "uploader_fallback", "uploaded_by_wins", "missing"],
    )
    def test_uploaded_by(self, prowlarr_service, raw, expected):
        release = prowlarr_service._parse_release_info(
            {"title": "Movie.2024.1080p.x264-RLSGRP", "indexer": "Test", **raw}
        )

        assert release.uploaded_by == expected

    def test_alternate_file_count_field(self, prowlarr_service):
        release = prowlarr_service._parse_release_info(
            {"title": "Movie", "indexer": "Test", "fileCount": 1}
        )

        assert release.files == 1

    @pytest.mark.parametrize("value", [False, -1, 1.5, "2"])
    def test_rejects_unsafe_file_counts(self, prowlarr_service, value):
        release = prowlarr_service._parse_release_info(
            {"title": "Movie", "indexer": "Test", "fileCount": value}
        )

        assert release.files is None
        assert release.file_metadata_observed_at is None

    def test_preserves_zero_and_safe_file_paths(self, prowlarr_service):
        release = prowlarr_service._parse_release_info(
            {
                "title": "Movie",
                "indexer": "Test",
                "files": 0,
                "fileCount": 3,
                "filePaths": ["Movie/movie.mkv", "Movie/movie.nfo"],
            }
        )

        assert release.files == 0
        assert release.file_paths == ("Movie/movie.mkv", "Movie/movie.nfo")
        assert release.file_metadata_observed_at is not None


class TestExtractReleaseItems:
    @pytest.mark.parametrize(
        "payload",
        [
            [{"title": "Return.to.Me.2000", "downloadUrl": "https://example.com/a.torrent"}],
            [
                {
                    "indexer": "IPT",
                    "releases": [
                        {"title": "Return.to.Me.2000", "downloadUrl": "https://example.com/a"}
                    ],
                }
            ],
        ],
        ids=["flat", "nested"],
    )
    def test_flat_and_nested_payloads(self, payload):
        releases = ProwlarrService._extract_release_items(payload)

        assert [r["title"] for r in releases] == ["Return.to.Me.2000"]


class TestQueryBuilding:
    @pytest.mark.parametrize(
        ("build", "expected"),
        [
            (
                lambda: ProwlarrService._build_movie_query("Return to Me", 1234, 2000),
                "Return to Me {tmdbid:1234} {year:2000}",
            ),
            (
                lambda: ProwlarrService._build_tv_query(
                    "Example Show", 5678, season=1, episode=2, year=2024
                ),
                "Example Show {tvdbid:5678} {season:1} {episode:2} {year:2024}",
            ),
            (
                lambda: ProwlarrService._build_tv_query("Example Show", 5678, season=8, year=2024),
                "Example Show {tvdbid:5678} {season:8} {year:2024}",
            ),
            (
                lambda: ProwlarrService._build_tv_query("Example Show", 5678, year=2024),
                "Example Show {tvdbid:5678} {year:2024}",
            ),
            (
                lambda: ProwlarrService._build_tv_title_query("Example Show", season=8, year=2024),
                "Example Show S08 2024",
            ),
        ],
        ids=["movie", "tv_episode", "tv_season_only", "tv_broad", "tv_title_season_only"],
    )
    def test_query_strings(self, build, expected):
        assert build() == expected

    @pytest.mark.parametrize(
        "kwargs",
        [{"imdbid": "tt7587890", "tvdbid": 350665}, {}],
        ids=["with_metadata", "without_metadata"],
    )
    def test_season_strategy_uses_one_canonical_title_query(self, kwargs):
        service = ProwlarrService(
            Settings(
                prowlarr_tv_strategy_title_sxx_enabled=True,
                prowlarr_tv_strategy_imdb_enabled=True,
                prowlarr_tv_strategy_tvdb_enabled=True,
            )
        )

        assert service._tv_season_strategy_queries("The Rookie", 1, **kwargs) == [
            ("title_sxx", "search", "The Rookie S01")
        ]


class TestSearchMethods:
    @pytest.mark.parametrize(
        ("method", "args", "expected_query"),
        [
            ("search_tv_episode_exact", ("Margo's Show", 2, 3), "Margos Show S02E03"),
            ("search_tv_packs_broad", ("Margo's Show",), "Margos Show"),
        ],
        ids=["episode_exact", "packs_broad"],
    )
    async def test_normalized_single_title_query(
        self, prowlarr_service, search_calls, method, args, expected_query
    ):
        calls, _ = search_calls

        await getattr(prowlarr_service, method)(*args)

        assert calls == [{"type": "search", "query": expected_query, "categories": [5000]}]

    @pytest.mark.parametrize(
        ("call", "first", "fallback"),
        [
            (
                lambda s: s.search_by_tmdbid(2621, title="Return to Me", year=2000),
                ("movie", "Return to Me {tmdbid:2621} {year:2000}"),
                "Return to Me 2000",
            ),
            (
                lambda s: s.search_by_tvdbid(
                    5678, title="Example Show", season=1, episode=2, year=2024
                ),
                ("tvsearch", "Example Show {tvdbid:5678} {season:1} {episode:2} {year:2024}"),
                "Example Show S01E02 2024",
            ),
        ],
        ids=["tmdbid", "tvdbid"],
    )
    async def test_empty_metadata_search_falls_back_to_title_query(
        self, prowlarr_service, search_calls, call, first, fallback
    ):
        calls, _ = search_calls

        await call(prowlarr_service)

        assert (calls[0]["type"], calls[0]["query"]) == first
        assert (calls[1]["type"], calls[1]["query"]) == ("search", fallback)

    async def test_unrelated_metadata_hit_does_not_suppress_title_fallback(self, prowlarr_service):
        wrong = _result(_release(1, "Burden of Truth S04E03 From Out the Gloomy Rack"))
        correct = _result(_release(2, "FROM S04E03 1080p"), ms=20)
        search = AsyncMock(side_effect=[wrong, correct])
        prowlarr_service._search = search

        result = await prowlarr_service.search_by_tvdbid(123, title="FROM", season=4, episode=3)

        assert result.releases[0].title == "FROM S04E03 1080p"
        assert search.await_count == 2
        assert search.await_args_list[1].args[0]["type"] == "search"
        assert wrong.releases, "A cached metadata result must not be mutated by identity filtering"

    async def test_correct_metadata_hit_does_not_trigger_an_extra_search(self, prowlarr_service):
        correct = _result(_release(1, "FROM S04E03 1080p"), ms=20)
        search = AsyncMock(return_value=correct)
        prowlarr_service._search = search

        result = await prowlarr_service.search_by_tvdbid(123, title="FROM", season=4, episode=3)

        assert result.releases == correct.releases
        search.assert_awaited_once()

    async def test_broad_search_tries_title_strategies_and_aggregates_unique_releases(
        self, prowlarr_service, search_calls
    ):
        calls, set_handler = search_calls
        by_fragment = {
            "S01-": _release(1, "Show S01-S03"),
            "complete": _release(2, "Show Complete"),
            "season 1-": _release(3, "Show Season 1-5"),
        }

        def handler(params):
            for fragment, release in by_fragment.items():
                if fragment in params["query"]:
                    return _result(release)
            return _result()

        set_handler(handler)

        result = await prowlarr_service.search_by_tvdbid(5678, title="The Mentalist", year=2024)

        assert [(c["type"], c["query"]) for c in calls] == [
            ("tvsearch", "The Mentalist {tvdbid:5678} {year:2024}"),
            ("search", "The Mentalist S01-"),
            ("search", "The Mentalist complete"),
            ("search", "The Mentalist season 1-"),
        ]
        assert len(result.releases) == 3
        assert result.query_time_ms == 40

    async def test_broad_search_deduplicates_by_url(self, prowlarr_service, search_calls):
        _, set_handler = search_calls
        shared = _release(1, "Show S01-S03", url="http://example.com/same")
        set_handler(lambda params: _result(shared))

        result = await prowlarr_service.search_by_tvdbid(5678, title="The Mentalist", year=2024)

        assert [r.download_url for r in result.releases] == ["http://example.com/same"]


SWEEP_SETTINGS = {
    "prowlarr_tv_page_size": 100,
    "prowlarr_tv_strategy_title_sxx_enabled": True,
    "prowlarr_tv_strategy_imdb_enabled": True,
    "prowlarr_tv_strategy_tvdb_enabled": True,
    "prowlarr_tv_strategy_title_season_token_enabled": False,
}


def _page(start: int, count: int) -> ProwlarrSearchResult:
    return _result(*[_release(start + i) for i in range(count)])


class TestSeasonSweep:
    async def test_page_request_sets_offset_without_limit(self, search_calls):
        calls, set_handler = search_calls
        set_handler(lambda params: _result(_release(1)))
        service = ProwlarrService(Settings.model_validate(SWEEP_SETTINGS))

        result = await service.search_tv_season_page("The Rookie", 1, "title_sxx", offset=100)

        assert calls == [
            {"type": "search", "query": "The Rookie S01", "categories": [5000], "offset": 100}
        ]
        assert (result.offset, result.page_size, result.page_count) == (100, 100, 1)
        assert result.is_short_page is True
        assert result.query_strategy == "title_sxx"

    @pytest.mark.parametrize(
        ("page_for", "expected_offsets", "expected_count"),
        [
            (
                lambda off: _page(off, 100 if off < 200 else 54),
                [0, 100, 200],
                254,
            ),
            (lambda off: _page(off, 100 if off == 0 else 0), [0, 100], 100),
            # Page 2 repeats 50 known releases and adds 50 new; page 3 adds nothing.
            (lambda off: _page(0 if off == 0 else 50, 100), [0, 100, 200], 150),
            # The same full page twice stops the sweep.
            (lambda off: _page(0, 100), [0, 100], 100),
            # Sweeps are not capped at the old 3 pages or 300 results.
            (
                lambda off: _page(off, 100 if off < 300 else 1),
                [0, 100, 200, 300],
                301,
            ),
        ],
        ids=[
            "short_page_stops",
            "empty_page_stops",
            "no_new_releases_stops",
            "repeated_full_page_stops",
            "continues_past_old_max",
        ],
    )
    async def test_paging_and_stop_conditions(
        self, search_calls, page_for, expected_offsets, expected_count
    ):
        calls, set_handler = search_calls
        set_handler(lambda params: page_for(params["offset"]))
        service = ProwlarrService(Settings.model_validate(SWEEP_SETTINGS))

        result = await service.search_tv_season_sweep(
            "The Rookie", 1, imdbid="tt7587890", tvdbid=350665
        )

        assert [call["offset"] for call in calls] == expected_offsets
        assert all("limit" not in call for call in calls)
        # Only the canonical title query runs, even with every strategy enabled.
        assert {call["query"] for call in calls} == {"The Rookie S01"}
        assert len(result.releases) == expected_count

    async def test_stops_at_max_pages_and_flags_limit(self, search_calls):
        calls, set_handler = search_calls
        set_handler(lambda params: _page(params["offset"], 100))
        service = ProwlarrService(
            Settings.model_validate({**SWEEP_SETTINGS, "prowlarr_tv_max_pages_per_strategy": 3})
        )

        result = await service.search_tv_season_sweep("The Rookie", 1)

        assert [call["offset"] for call in calls] == [0, 100, 200]
        assert len(result.releases) == 300
        assert result.hit_limit is True

    async def test_cancellation_keeps_completed_pages(self, search_calls):
        calls, set_handler = search_calls
        cancelled = False

        def handler(params):
            nonlocal cancelled
            cancelled = True
            return _page(0, 100)

        set_handler(handler)
        service = ProwlarrService(Settings(prowlarr_tv_page_size=100))

        result = await service.search_tv_season_sweep(
            "The Rookie", 1, cancellation_check=lambda: cancelled
        )

        assert [call["offset"] for call in calls] == [0]
        assert len(result.releases) == 100

    async def test_dedupes_volatile_download_urls_by_guid(self, search_calls):
        calls, set_handler = search_calls

        def handler(params):
            return _result(
                *[
                    ProwlarrRelease(
                        title=f"The Rookie S01E{i:02d}",
                        size=1000,
                        seeders=1,
                        leechers=0,
                        download_url=f"http://example.com/volatile/{params['offset']}/{i}",
                        guid=f"https://iptorrents.example/t/{i}",
                        indexer="IPTorrents",
                    )
                    for i in range(100)
                ]
            )

        set_handler(handler)
        service = ProwlarrService(Settings.model_validate(SWEEP_SETTINGS))

        result = await service.search_tv_season_sweep("The Rookie", 1)

        assert [call["offset"] for call in calls] == [0, 100]
        assert len(result.releases) == 100
