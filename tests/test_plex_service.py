"""Tests for PlexService movie and show lookup methods."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.siftarr.services.plex_service import PlexService


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

        # mock _get_movie_library_sections to return a section key
        monkeypatch.setattr(
            service,
            "_get_movie_library_sections",
            AsyncMock(return_value=["1"]),
        )

        # First two calls are guid search (400 each), third is section scan
        mock_client.get.return_value = error_response

        original_client = AsyncMock()

        async def get_client():
            return original_client

        monkeypatch.setattr(service, "_get_client", get_client)

        # guid searches fail
        original_client.get.return_value = error_response
        # But section scan returns results
        # We need to handle multiple calls: 2 guid searches (400), then 1 section scan (200)
        responses = [error_response, error_response, section_response]
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
        monkeypatch.setattr(service, "_get_tv_library_sections", AsyncMock(return_value=["2"]))

        # Reset client for fallback calls
        responses = []
        # 2 GUID searches return 400, then 1 section scan call
        for _ in range(2):
            responses.append(error_response)
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
