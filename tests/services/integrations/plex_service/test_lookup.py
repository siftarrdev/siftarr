from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from app.siftarr.services.plex_service import PlexService


def test_direct_metadata_format():
    container = {
        "Metadata": [
            {"type": "season", "ratingKey": "1", "title": "Season 1"},
            {"type": "season", "ratingKey": "2", "title": "Season 2"},
        ]
    }
    result = PlexService._extract_metadata_items(container)
    assert len(result) == 2
    assert result[0]["title"] == "Season 1"


def test_search_result_format():
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


def test_empty_container():
    assert PlexService._extract_metadata_items({}) == []
    assert PlexService._extract_metadata_items({"Metadata": []}) == []
    assert PlexService._extract_metadata_items({"SearchResult": []}) == []


def test_mixed_none_metadata():
    container = {
        "SearchResult": [
            {"Metadata": {"type": "show", "ratingKey": "1", "title": "A"}},
            {"Metadata": None},
        ]
    }
    result = PlexService._extract_metadata_items(container)
    assert len(result) == 1
    assert result[0]["title"] == "A"


@pytest.fixture
def service(service_factory):
    return service_factory()


@pytest.fixture
def mock_client(service, monkeypatch):
    client = AsyncMock()
    monkeypatch.setattr(service, "_get_client", AsyncMock(return_value=client))
    return client


def test_match_tmdb_guid(service):
    item = {"Guid": [{"id": "imdb://tt12345"}, {"id": "tmdb://79744"}, {"id": "tvdb://350665"}]}
    assert service._match_guid(item, "tmdb://", 79744) is True
    assert service._match_guid(item, "tvdb://", 350665) is True
    assert service._match_guid(item, "tmdb://", 99999) is False


def test_match_guid_missing(service):
    assert service._match_guid({"Guid": [{"id": "imdb://tt12345"}]}, "tmdb://", 79744) is False


def test_match_guid_empty(service):
    assert service._match_guid({"Guid": []}, "tmdb://", 79744) is False


def test_match_guid_no_guid_key(service):
    assert service._match_guid({}, "tmdb://", 79744) is False


@pytest.mark.asyncio
async def test_check_movie_available_true(service, mock_client):
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

    assert await service.check_movie_available(12345) is True


@pytest.mark.asyncio
async def test_check_movie_available_false_no_media(service, mock_client):
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

    assert await service.check_movie_available(12345) is False


@pytest.mark.asyncio
async def test_check_movie_available_false_not_found(service, mock_client):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"MediaContainer": {"Metadata": []}}
    mock_client.get.return_value = mock_response

    assert await service.check_movie_available(99999) is False


@pytest.mark.asyncio
async def test_check_movie_available_false_http_error(service, mock_client):
    mock_response = MagicMock()
    mock_response.status_code = 400
    mock_client.get.return_value = mock_response

    assert await service.check_movie_available(12345) is False


@pytest.mark.asyncio
async def test_get_movie_by_tmdb_returns_metadata(service, mock_client):
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
async def test_get_movie_by_tmdb_modern_guid_format(service, mock_client):
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
async def test_get_movie_by_tmdb_fallback_to_section_scan(service, mock_client, monkeypatch):
    error_response = MagicMock()
    error_response.status_code = 400
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
                    "Guid": [{"id": "tmdb://555"}],
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

    original_client.get.return_value = error_response
    original_client.get.side_effect = [
        error_response,
        error_response,
        sections_response,
        section_response,
    ]

    result = await service.get_movie_by_tmdb(555)
    assert result is not None
    assert result["rating_key"] == "999"
    assert result["title"] == "Scanned Movie"


@pytest.mark.asyncio
async def test_get_movie_by_tmdb_no_config():
    settings = MagicMock()
    settings.plex_url = None
    settings.plex_token = None
    svc = PlexService(settings=settings)
    assert await svc.get_movie_by_tmdb(123) is None


@pytest.mark.asyncio
async def test_check_movie_available_no_config():
    settings = MagicMock()
    settings.plex_url = None
    settings.plex_token = None
    svc = PlexService(settings=settings)
    assert await svc.check_movie_available(123) is False


@pytest.mark.asyncio
async def test_get_show_by_tmdb_search_result_format(service, mock_client):
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
                }
            ]
        }
    }
    mock_client.get.return_value = mock_response

    result = await service.get_show_by_tmdb(79744)
    assert result is not None
    assert result["rating_key"] == "25620"
    assert result["title"] == "The Rookie"


@pytest.mark.asyncio
async def test_get_show_by_tmdb_legacy_guid_fallback(service, mock_client):
    modern_response = MagicMock()
    modern_response.status_code = 200
    modern_response.json.return_value = {"MediaContainer": {}}
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
async def test_get_show_by_tvdb_search_result_format(service, mock_client):
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
                }
            ]
        }
    }
    mock_client.get.return_value = mock_response

    result = await service.get_show_by_tvdb(12345)
    assert result is not None
    assert result["rating_key"] == "300"


@pytest.mark.asyncio
async def test_get_show_by_tmdb_section_scan_fallback(service, monkeypatch):
    client = AsyncMock()
    error_response = MagicMock()
    error_response.status_code = 400
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
                }
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

    client.get.side_effect = [error_response, error_response, sections_response, section_response]

    result = await service.get_show_by_tmdb(79744)
    assert result is not None
    assert result["rating_key"] == "25620"
    assert result["title"] == "The Rookie"


@pytest.mark.asyncio
async def test_search_show_search_result_format(service, mock_client):
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
    assert len(result) == 2
    assert result[0]["title"] == "The Rookie"
    assert result[0]["rating_key"] == "25620"
    assert result[1]["title"] == "The Rookie: Feds"


@pytest.mark.asyncio
async def test_get_show_by_tmdb_no_config():
    settings = MagicMock()
    settings.plex_url = None
    settings.plex_token = None
    svc = PlexService(settings=settings)
    assert await svc.get_show_by_tmdb(79744) is None


@pytest.mark.asyncio
async def test_get_show_by_tvdb_no_config():
    settings = MagicMock()
    settings.plex_url = None
    settings.plex_token = None
    svc = PlexService(settings=settings)
    assert await svc.get_show_by_tvdb(350665) is None


@pytest.mark.asyncio
async def test_lookup_show_by_tvdb_reports_inconclusive_on_section_failure(service, mock_client):
    sections_response = MagicMock()
    sections_response.status_code = 200
    sections_response.json.return_value = {
        "MediaContainer": {
            "Directory": [{"key": "2", "type": "show"}, {"key": "3", "type": "show"}]
        }
    }
    empty_search = MagicMock()
    empty_search.status_code = 200
    empty_search.json.return_value = {"MediaContainer": {}}
    good_scan = MagicMock()
    good_scan.status_code = 200
    good_scan.json.return_value = {"MediaContainer": {"size": 0, "totalSize": 0, "Metadata": []}}

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
