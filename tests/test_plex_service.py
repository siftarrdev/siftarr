"""Tests for PlexService movie and show lookup methods."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from app.siftarr.services.plex_service import (
    PlexEpisodeAvailabilityResult,
    PlexLibraryScanResult,
    PlexService,
    PlexTransientScanError,
)


class TestPlexServiceExtractMetadata:
    """Test cases for _extract_metadata_items helper."""

    def test_direct_metadata_format(self):
        """Plex endpoints like /metadata/ID/children return Metadata[] directly."""
        container = {
            "Metadata": [
                {"type": "season", "ratingKey": "1", "title": "Season 1"},
                {"type": "season", "ratingKey": "2", "title": "Season 2"},
            ]
        }
        result = PlexService._extract_metadata_items(container)
        assert len(result) == 2
        assert result[0]["title"] == "Season 1"

    def test_search_result_format(self):
        """Plex /library/search returns SearchResult[].Metadata."""
        container = {
            "SearchResult": [
                {
                    "score": 0.93,
                    "Metadata": {"type": "show", "ratingKey": "100", "title": "The Rookie"},
                },
                {
                    "score": 0.85,
                    "Metadata": {"type": "movie", "ratingKey": "200", "title": "Some Movie"},
                },
            ]
        }
        result = PlexService._extract_metadata_items(container)
        assert len(result) == 2
        assert result[0]["title"] == "The Rookie"
        assert result[1]["title"] == "Some Movie"

    def test_empty_container(self):
        """Empty container returns empty list."""
        assert PlexService._extract_metadata_items({}) == []
        assert PlexService._extract_metadata_items({"Metadata": []}) == []
        assert PlexService._extract_metadata_items({"SearchResult": []}) == []

    def test_mixed_none_metadata(self):
        """SearchResult entries with null Metadata are skipped."""
        container = {
            "SearchResult": [
                {"Metadata": {"type": "show", "ratingKey": "1", "title": "A"}},
                {"Metadata": None},
            ]
        }
        result = PlexService._extract_metadata_items(container)
        assert len(result) == 1
        assert result[0]["title"] == "A"


class TestPlexServiceMatchGuid:
    """Test cases for _match_guid helper."""

    @pytest.fixture
    def service(self):
        settings = MagicMock()
        settings.plex_url = "http://plex:32400"
        settings.plex_token = "test-token"
        return PlexService(settings=settings)

    def test_match_tmdb_guid(self, service):
        item = {
            "Guid": [
                {"id": "imdb://tt12345"},
                {"id": "tmdb://79744"},
                {"id": "tvdb://350665"},
            ]
        }
        assert service._match_guid(item, "tmdb://", 79744) is True
        assert service._match_guid(item, "tvdb://", 350665) is True
        assert service._match_guid(item, "tmdb://", 99999) is False

    def test_match_guid_missing(self, service):
        item = {"Guid": [{"id": "imdb://tt12345"}]}
        assert service._match_guid(item, "tmdb://", 79744) is False

    def test_match_guid_empty(self, service):
        item = {"Guid": []}
        assert service._match_guid(item, "tmdb://", 79744) is False

    def test_match_guid_no_guid_key(self, service):
        item = {}
        assert service._match_guid(item, "tmdb://", 79744) is False


class TestPlexServiceMovie:
    """Test cases for PlexService movie lookup methods."""

    @pytest.fixture
    def service(self):
        settings = MagicMock()
        settings.plex_url = "http://plex:32400"
        settings.plex_token = "test-token"
        svc = PlexService(settings=settings)
        return svc

    @pytest.fixture
    def mock_client(self, service, monkeypatch):
        client = AsyncMock()
        monkeypatch.setattr(service, "_get_client", AsyncMock(return_value=client))
        return client

    @pytest.mark.asyncio
    async def test_check_movie_available_true(self, service, mock_client):
        """Movie with Media entries is available."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "MediaContainer": {
                "Metadata": [
                    {
                        "type": "movie",
                        "ratingKey": "123",
                        "title": "Test Movie",
                        "year": 2024,
                        "guid": "plex://movie/abc",
                        "Media": [{"id": 1}],
                    }
                ]
            }
        }
        mock_client.get.return_value = mock_response

        result = await service.check_movie_available(12345)
        assert result is True

    @pytest.mark.asyncio
    async def test_check_movie_available_false_no_media(self, service, mock_client):
        """Movie without Media entries is not available."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "MediaContainer": {
                "Metadata": [
                    {
                        "type": "movie",
                        "ratingKey": "123",
                        "title": "Test Movie",
                        "year": 2024,
                        "guid": "plex://movie/abc",
                    }
                ]
            }
        }
        mock_client.get.return_value = mock_response

        result = await service.check_movie_available(12345)
        assert result is False

    @pytest.mark.asyncio
    async def test_check_movie_available_false_not_found(self, service, mock_client):
        """Movie not in Plex returns False."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"MediaContainer": {"Metadata": []}}
        mock_client.get.return_value = mock_response

        result = await service.check_movie_available(99999)
        assert result is False

    @pytest.mark.asyncio
    async def test_check_movie_available_false_http_error(self, service, mock_client):
        """HTTP error returns False."""
        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_client.get.return_value = mock_response

        result = await service.check_movie_available(12345)
        assert result is False

    @pytest.mark.asyncio
    async def test_get_movie_by_tmdb_returns_metadata(self, service, mock_client):
        """get_movie_by_tmdb returns movie metadata dict."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "MediaContainer": {
                "Metadata": [
                    {
                        "type": "movie",
                        "ratingKey": "456",
                        "title": "A Movie",
                        "year": 2023,
                        "guid": "plex://movie/xyz",
                        "Media": [{"id": 2}],
                    }
                ]
            }
        }
        mock_client.get.return_value = mock_response

        result = await service.get_movie_by_tmdb(555)
        assert result is not None
        assert result["rating_key"] == "456"
        assert result["title"] == "A Movie"

    @pytest.mark.asyncio
    async def test_get_movie_by_tmdb_modern_guid_format(self, service, mock_client):
        """get_movie_by_tmdb tries modern tmdb:// guid format first."""
        # First call (modern format) returns 200 with result
        modern_response = MagicMock()
        modern_response.status_code = 200
        modern_response.json.return_value = {
            "MediaContainer": {
                "Metadata": [
                    {
                        "type": "movie",
                        "ratingKey": "789",
                        "title": "Modern Movie",
                        "year": 2024,
                        "guid": "plex://movie/abc",
                        "Media": [{"id": 3}],
                    }
                ]
            }
        }

        mock_client.get.return_value = modern_response
        result = await service.get_movie_by_tmdb(555)

        assert result is not None
        assert result["rating_key"] == "789"
        assert result["title"] == "Modern Movie"

    @pytest.mark.asyncio
    async def test_get_movie_by_tmdb_fallback_to_section_scan(
        self, service, mock_client, monkeypatch
    ):
        """get_movie_by_tmdb falls back to section scan when guid search fails."""
        # All guid searches return 400
        error_response = MagicMock()
        error_response.status_code = 400

        # Section scan returns the movie
        section_response = MagicMock()
        section_response.status_code = 200
        section_response.json.return_value = {
            "MediaContainer": {
                "Metadata": [
                    {
                        "type": "movie",
                        "ratingKey": "999",
                        "title": "Scanned Movie",
                        "year": 2024,
                        "guid": "plex://movie/xyz",
                        "Guid": [
                            {"id": "tmdb://555"},
                        ],
                        "Media": [{"id": 5}],
                    }
                ]
            }
        }

        original_client = AsyncMock()

        async def get_client():
            return original_client

        monkeypatch.setattr(service, "_get_client", get_client)

        sections_response = MagicMock()
        sections_response.status_code = 200
        sections_response.json.return_value = {
            "MediaContainer": {"Directory": [{"key": "1", "type": "movie"}]}
        }

        # guid searches fail
        original_client.get.return_value = error_response
        # We need to handle multiple calls: 2 guid searches (400), section listing, then scan (200)
        responses = [error_response, error_response, sections_response, section_response]
        original_client.get.side_effect = responses

        result = await service.get_movie_by_tmdb(555)
        assert result is not None
        assert result["rating_key"] == "999"
        assert result["title"] == "Scanned Movie"

    @pytest.mark.asyncio
    async def test_get_movie_by_tmdb_no_config(self):
        """Returns None when Plex is not configured."""
        settings = MagicMock()
        settings.plex_url = None
        settings.plex_token = None
        svc = PlexService(settings=settings)

        result = await svc.get_movie_by_tmdb(123)
        assert result is None

    @pytest.mark.asyncio
    async def test_check_movie_available_no_config(self):
        """Returns False when Plex is not configured."""
        settings = MagicMock()
        settings.plex_url = None
        settings.plex_token = None
        svc = PlexService(settings=settings)

        result = await svc.check_movie_available(123)
        assert result is False


class TestPlexServiceShowLookup:
    """Test cases for PlexService show lookup by TMDB/TVDB ID."""

    @pytest.fixture
    def service(self):
        settings = MagicMock()
        settings.plex_url = "http://plex:32400"
        settings.plex_token = "test-token"
        svc = PlexService(settings=settings)
        return svc

    @pytest.fixture
    def mock_client(self, service, monkeypatch):
        client = AsyncMock()
        monkeypatch.setattr(service, "_get_client", AsyncMock(return_value=client))
        return client

    @pytest.mark.asyncio
    async def test_get_show_by_tmdb_search_result_format(self, service, mock_client):
        """get_show_by_tmdb handles SearchResult wrapper from /library/search."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "MediaContainer": {
                "SearchResult": [
                    {
                        "score": 0.93,
                        "Metadata": {
                            "type": "show",
                            "ratingKey": "25620",
                            "title": "The Rookie",
                            "year": 2018,
                            "guid": "plex://show/5d9c08ffe264b7001fc4d397",
                        },
                    },
                ]
            }
        }
        mock_client.get.return_value = mock_response

        result = await service.get_show_by_tmdb(79744)
        assert result is not None
        assert result["rating_key"] == "25620"
        assert result["title"] == "The Rookie"

    @pytest.mark.asyncio
    async def test_get_show_by_tmdb_legacy_guid_fallback(self, service, mock_client):
        """get_show_by_tmdb tries legacy guid format if modern format returns no results."""
        # Modern format returns empty
        modern_response = MagicMock()
        modern_response.status_code = 200
        modern_response.json.return_value = {"MediaContainer": {}}

        # Legacy format returns the show
        legacy_response = MagicMock()
        legacy_response.status_code = 200
        legacy_response.json.return_value = {
            "MediaContainer": {
                "Metadata": [
                    {
                        "type": "show",
                        "ratingKey": "100",
                        "title": "Legacy Show",
                        "year": 2020,
                        "guid": "com.plexapp.agents.themoviedb://12345?lang=en",
                    }
                ]
            }
        }

        mock_client.get.side_effect = [modern_response, legacy_response]
        result = await service.get_show_by_tmdb(12345)
        assert result is not None
        assert result["rating_key"] == "100"
        assert result["title"] == "Legacy Show"

    @pytest.mark.asyncio
    async def test_get_show_by_tvdb_search_result_format(self, service, mock_client):
        """get_show_by_tvdb handles SearchResult wrapper from /library/search."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "MediaContainer": {
                "SearchResult": [
                    {
                        "score": 0.90,
                        "Metadata": {
                            "type": "show",
                            "ratingKey": "300",
                            "title": "A TV Show",
                            "year": 2019,
                            "guid": "plex://show/abc",
                        },
                    },
                ]
            }
        }
        mock_client.get.return_value = mock_response

        result = await service.get_show_by_tvdb(12345)
        assert result is not None
        assert result["rating_key"] == "300"

    @pytest.mark.asyncio
    async def test_get_show_by_tmdb_section_scan_fallback(self, service, monkeypatch):
        """get_show_by_tmdb falls back to section scan when guid search fails."""
        client = AsyncMock()

        # guid searches return 400
        error_response = MagicMock()
        error_response.status_code = 400

        # section scan returns results with matching Guid
        section_response = MagicMock()
        section_response.status_code = 200
        section_response.json.return_value = {
            "MediaContainer": {
                "Metadata": [
                    {
                        "type": "show",
                        "ratingKey": "25620",
                        "title": "The Rookie",
                        "year": 2018,
                        "guid": "plex://show/5d9c08ffe264b7001fc4d397",
                        "Guid": [
                            {"id": "imdb://tt7587890"},
                            {"id": "tmdb://79744"},
                            {"id": "tvdb://350665"},
                        ],
                    },
                ]
            }
        }

        client.get.return_value = error_response
        monkeypatch.setattr(service, "_get_client", AsyncMock(return_value=client))

        sections_response = MagicMock()
        sections_response.status_code = 200
        sections_response.json.return_value = {
            "MediaContainer": {"Directory": [{"key": "2", "type": "show"}]}
        }

        # Reset client for fallback calls
        responses = []
        # 2 GUID searches return 400, then section listing, then 1 section scan call
        for _ in range(2):
            responses.append(error_response)
        responses.append(sections_response)
        responses.append(section_response)
        client.get.side_effect = responses

        result = await service.get_show_by_tmdb(79744)
        assert result is not None
        assert result["rating_key"] == "25620"
        assert result["title"] == "The Rookie"

    @pytest.mark.asyncio
    async def test_search_show_search_result_format(self, service, mock_client):
        """search_show handles SearchResult wrapper from /library/search."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "MediaContainer": {
                "SearchResult": [
                    {
                        "score": 0.93,
                        "Metadata": {
                            "type": "show",
                            "ratingKey": "25620",
                            "title": "The Rookie",
                            "year": 2018,
                            "guid": "plex://show/5d9c08ffe264b7001fc4d397",
                        },
                    },
                    {
                        "score": 0.85,
                        "Metadata": {
                            "type": "show",
                            "ratingKey": "25736",
                            "title": "The Rookie: Feds",
                            "year": 2022,
                            "guid": "plex://show/627fcfb77eb52ccdcc843c0c",
                        },
                    },
                    {
                        "score": 0.31,
                        "Metadata": {
                            "type": "episode",
                            "ratingKey": "25622",
                            "title": "Pilot",
                            "year": 2018,
                        },
                    },
                ]
            }
        }
        mock_client.get.return_value = mock_response

        result = await service.search_show("The Rookie")
        assert len(result) == 2  # Episode filtered out
        assert result[0]["title"] == "The Rookie"
        assert result[0]["rating_key"] == "25620"
        assert result[1]["title"] == "The Rookie: Feds"

    @pytest.mark.asyncio
    async def test_get_show_by_tmdb_no_config(self):
        """Returns None when Plex is not configured."""
        settings = MagicMock()
        settings.plex_url = None
        settings.plex_token = None
        svc = PlexService(settings=settings)

        result = await svc.get_show_by_tmdb(79744)
        assert result is None

    @pytest.mark.asyncio
    async def test_get_show_by_tvdb_no_config(self):
        """Returns None when Plex is not configured."""
        settings = MagicMock()
        settings.plex_url = None
        settings.plex_token = None
        svc = PlexService(settings=settings)

        result = await svc.get_show_by_tvdb(350665)
        assert result is None


class TestPlexServiceEpisodes:
    """Test cases for PlexService episode availability methods."""

    @pytest.fixture
    def service(self):
        settings = MagicMock()
        settings.plex_url = "http://plex:32400"
        settings.plex_token = "test-token"
        settings.plex_sync_concurrency = 2
        return PlexService(settings=settings)

    @pytest.mark.asyncio
    async def test_get_episode_availability_uses_bounded_parallel_fetches(
        self, service, monkeypatch
    ):
        """Season child fetches overlap without exceeding configured concurrency."""
        seasons = [
            {"type": "season", "index": 1, "ratingKey": "season-1"},
            {"type": "season", "index": 2, "ratingKey": "season-2"},
            {"type": "season", "index": 3, "ratingKey": "season-3"},
        ]
        season_episodes = {
            "season-1": [{"type": "episode", "index": 1, "Media": [{"id": 1}]}],
            "season-2": [{"type": "episode", "index": 2}],
            "season-3": [{"type": "episode", "index": 3, "Media": [{"id": 3}]}],
        }
        started: list[str] = []
        released: dict[str, asyncio.Event] = {key: asyncio.Event() for key in season_episodes}
        in_flight = 0
        max_in_flight = 0
        first_batch_ready = asyncio.Event()
        third_started = asyncio.Event()
        lock = asyncio.Lock()

        async def get_show_children(_: str):
            return seasons

        async def get_season_children(season_rating_key: str):
            nonlocal in_flight, max_in_flight
            async with lock:
                started.append(season_rating_key)
                in_flight += 1
                max_in_flight = max(max_in_flight, in_flight)
                if in_flight == 2:
                    first_batch_ready.set()
                if len(started) == 3:
                    third_started.set()

            await released[season_rating_key].wait()

            async with lock:
                in_flight -= 1

            return season_episodes[season_rating_key]

        monkeypatch.setattr(service, "get_show_children", get_show_children)
        monkeypatch.setattr(service, "get_season_children", get_season_children)

        availability_task = asyncio.create_task(service.get_episode_availability("show-1"))

        await asyncio.wait_for(first_batch_ready.wait(), timeout=1)

        assert max_in_flight == 2
        assert third_started.is_set() is False

        released["season-1"].set()
        await asyncio.wait_for(third_started.wait(), timeout=1)
        assert max_in_flight == 2

        released["season-2"].set()
        released["season-3"].set()

        availability = await availability_task

        assert availability == {
            (1, 1): True,
            (2, 2): False,
            (3, 3): True,
        }
        assert max_in_flight == 2

    @pytest.mark.asyncio
    async def test_get_episode_availability_preserves_deterministic_filtering(
        self, service, monkeypatch
    ):
        """Non-season and non-episode entries are ignored consistently."""

        async def get_show_children(_: str):
            return [
                {"type": "season", "index": 2, "ratingKey": "season-2"},
                {"type": "artist", "index": 99, "ratingKey": "ignored"},
                {"type": "season", "ratingKey": "missing-index"},
                {"type": "season", "index": 1, "ratingKey": "season-1"},
                {"type": "season", "index": 3},
            ]

        async def get_season_children(season_rating_key: str):
            return {
                "season-1": [
                    {"type": "clip", "index": 9, "Media": [{"id": 1}]},
                    {"type": "episode", "Media": [{"id": 1}]},
                    {"type": "episode", "index": 1, "Media": [{"id": 1}]},
                ],
                "season-2": [
                    {"type": "episode", "index": 2},
                    {"type": "episode", "index": 1, "Media": [{"id": 2}]},
                ],
            }[season_rating_key]

        monkeypatch.setattr(service, "get_show_children", get_show_children)
        monkeypatch.setattr(service, "get_season_children", get_season_children)

        availability = await service.get_episode_availability("show-1")

        assert availability == {
            (2, 2): False,
            (2, 1): True,
            (1, 1): True,
        }


class TestPlexServiceScanPrimitives:
    """Test cases for scan iterators, caches, and authoritative lookups."""

    @pytest.fixture
    def service(self):
        settings = MagicMock()
        settings.plex_url = "http://plex:32400"
        settings.plex_token = "test-token"
        settings.plex_sync_concurrency = 2
        return PlexService(settings=settings)

    @pytest.fixture
    def mock_client(self, service, monkeypatch):
        client = AsyncMock()
        monkeypatch.setattr(service, "_get_client", AsyncMock(return_value=client))
        return client

    @pytest.mark.asyncio
    async def test_iter_full_library_items_uses_pagination(self, service, mock_client):
        """Full-library scans page through section contents."""
        sections_response = MagicMock()
        sections_response.status_code = 200
        sections_response.json.return_value = {
            "MediaContainer": {"Directory": [{"key": "7", "type": "movie", "title": "Movies"}]}
        }

        page_one = MagicMock()
        page_one.status_code = 200
        page_one.json.return_value = {
            "MediaContainer": {
                "size": 2,
                "totalSize": 3,
                "Metadata": [
                    {
                        "type": "movie",
                        "ratingKey": "101",
                        "title": "One",
                        "Guid": [{"id": "tmdb://1"}],
                    },
                    {
                        "type": "movie",
                        "ratingKey": "102",
                        "title": "Two",
                        "Guid": [{"id": "tmdb://2"}],
                    },
                ],
            }
        }

        page_two = MagicMock()
        page_two.status_code = 200
        page_two.json.return_value = {
            "MediaContainer": {
                "size": 1,
                "totalSize": 3,
                "Metadata": [
                    {
                        "type": "movie",
                        "ratingKey": "103",
                        "title": "Three",
                        "Guid": [{"id": "tmdb://3"}],
                    }
                ],
            }
        }

        mock_client.get.side_effect = [sections_response, page_one, page_two]

        items = [item async for item in service.iter_full_library_items("movie", page_size=2)]

        assert [item["rating_key"] for item in items] == ["101", "102", "103"]
        assert items[0]["section_key"] == "7"
        assert items[0]["guids"] == ("tmdb://1",)
        assert mock_client.get.await_args_list[1].kwargs["params"]["X-Plex-Container-Start"] == "0"
        assert mock_client.get.await_args_list[2].kwargs["params"]["X-Plex-Container-Start"] == "2"

    @pytest.mark.asyncio
    async def test_iter_recently_added_items_uses_recently_added_endpoint(
        self, service, mock_client
    ):
        """Recently-added scans use the dedicated Plex endpoint."""
        sections_response = MagicMock()
        sections_response.status_code = 200
        sections_response.json.return_value = {
            "MediaContainer": {"Directory": [{"key": "2", "type": "show"}]}
        }

        recent_response = MagicMock()
        recent_response.status_code = 200
        recent_response.json.return_value = {
            "MediaContainer": {
                "size": 1,
                "totalSize": 1,
                "Metadata": [
                    {
                        "type": "show",
                        "ratingKey": "401",
                        "title": "Recent Show",
                        "Guid": [{"id": "tvdb://123"}],
                        "addedAt": 1710000000,
                    }
                ],
            }
        }

        mock_client.get.side_effect = [sections_response, recent_response]

        items = [item async for item in service.iter_recently_added_items("show")]

        assert len(items) == 1
        assert items[0]["rating_key"] == "401"
        assert items[0]["added_at"] == 1710000000
        assert "/library/sections/2/recentlyAdded" in mock_client.get.await_args_list[1].args[0]

    @pytest.mark.asyncio
    async def test_scan_cycle_caches_section_listing_and_lookup_results(self, service, mock_client):
        """Repeated lookups in one scan cycle re-use cached Plex data."""
        sections_response = MagicMock()
        sections_response.status_code = 200
        sections_response.json.return_value = {
            "MediaContainer": {"Directory": [{"key": "9", "type": "movie"}]}
        }

        empty_search = MagicMock()
        empty_search.status_code = 200
        empty_search.json.return_value = {"MediaContainer": {}}

        section_scan = MagicMock()
        section_scan.status_code = 200
        section_scan.json.return_value = {
            "MediaContainer": {
                "size": 1,
                "totalSize": 1,
                "Metadata": [
                    {
                        "type": "movie",
                        "ratingKey": "900",
                        "title": "Cached Movie",
                        "Guid": [{"id": "tmdb://444"}],
                        "Media": [{"id": 1}],
                    }
                ],
            }
        }

        mock_client.get.side_effect = [
            empty_search,
            empty_search,
            sections_response,
            section_scan,
        ]

        async with service.scan_cycle():
            first = await service.lookup_movie_by_tmdb(444)
            second = await service.lookup_movie_by_tmdb(444)
            cached_item = service.get_cached_item_by_rating_key("900")

        assert first.item is not None
        assert first.authoritative is True
        assert second.item is not None
        assert second.item["rating_key"] == "900"
        assert mock_client.get.await_count == 4
        assert cached_item is not None
        assert cached_item["title"] == "Cached Movie"

    @pytest.mark.asyncio
    async def test_lookup_show_by_tvdb_reports_inconclusive_on_section_failure(
        self, service, mock_client
    ):
        """Transient section failures are reported as non-authoritative misses."""
        sections_response = MagicMock()
        sections_response.status_code = 200
        sections_response.json.return_value = {
            "MediaContainer": {
                "Directory": [
                    {"key": "2", "type": "show"},
                    {"key": "3", "type": "show"},
                ]
            }
        }

        empty_search = MagicMock()
        empty_search.status_code = 200
        empty_search.json.return_value = {"MediaContainer": {}}

        good_scan = MagicMock()
        good_scan.status_code = 200
        good_scan.json.return_value = {
            "MediaContainer": {"size": 0, "totalSize": 0, "Metadata": []}
        }

        mock_client.get.side_effect = [
            empty_search,
            empty_search,
            sections_response,
            good_scan,
            httpx.RequestError("boom"),
        ]

        result = await service.lookup_show_by_tvdb(777)

        assert result.item is None
        assert result.authoritative is False
        assert result.failed_sections == ("3",)

    @pytest.mark.asyncio
    async def test_iter_section_items_raises_transient_error_on_http_failure(
        self, service, mock_client
    ):
        """Section iterators raise a transient error for failed scans."""
        mock_client.get.side_effect = httpx.RequestError("network")

        with pytest.raises(PlexTransientScanError):
            [item async for item in service.iter_section_items("5")]

    @pytest.mark.asyncio
    async def test_scan_library_items_reports_partial_failure_authoritatively(
        self, service, monkeypatch
    ):
        """Full scans should preserve scanned items while flagging failed sections."""

        async def get_sections(media_type: str, *, strict: bool):
            assert media_type == "movie"
            assert strict is True
            return [{"key": "1", "type": "movie"}, {"key": "2", "type": "movie"}]

        async def iter_section_items(section_key: str, **kwargs):
            if section_key == "1":
                yield {"rating_key": "101", "type": "movie", "Media": [{"id": 1}]}
                return
            raise PlexTransientScanError("boom")
            if False:
                yield {}

        monkeypatch.setattr(service, "_get_library_sections_metadata", get_sections)
        monkeypatch.setattr(service, "iter_section_items", iter_section_items)

        result = await service.scan_library_items("movie")

        assert result == PlexLibraryScanResult(
            media_type="movie",
            items=({"rating_key": "101", "type": "movie", "Media": [{"id": 1}]},),
            authoritative=False,
            failed_sections=("2",),
        )

    @pytest.mark.asyncio
    async def test_get_episode_availability_result_returns_inconclusive_on_season_failure(
        self, service, monkeypatch
    ):
        """Episode availability should stay non-authoritative on transient child failures."""

        async def get_children(rating_key: str):
            if rating_key == "show-1":
                return [{"type": "season", "index": 1, "ratingKey": "season-1"}]
            raise PlexTransientScanError("network")

        monkeypatch.setattr(service, "_get_metadata_children_strict", get_children)

        result = await service.get_episode_availability_result("show-1")

        assert result == PlexEpisodeAvailabilityResult(availability={}, authoritative=False)
