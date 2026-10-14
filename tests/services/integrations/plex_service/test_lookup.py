from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from app.siftarr.services.integrations.plex_service import PlexService


def resp(container: dict | None = None, status: int = 200) -> MagicMock:
    response = MagicMock()
    response.status_code = status
    response.json.return_value = {"MediaContainer": container if container is not None else {}}
    return response


def movie(rating_key: str, title: str, *, media: bool = True, **extra) -> dict:
    item = {
        "type": "movie",
        "ratingKey": rating_key,
        "title": title,
        "year": 2024,
        "guid": "plex://movie/abc",
        **extra,
    }
    if media:
        item["Media"] = [{"id": 1}]
    return item


def show(rating_key: str, title: str, **extra) -> dict:
    return {"type": "show", "ratingKey": rating_key, "title": title, "year": 2018, **extra}


@pytest.fixture
def service(service_factory):
    return service_factory()


@pytest.fixture
def mock_client(service, monkeypatch):
    client = AsyncMock()
    monkeypatch.setattr(service, "_get_client", AsyncMock(return_value=client))
    return client


@pytest.fixture
def unconfigured_service():
    settings = MagicMock()
    settings.plex_url = None
    settings.plex_token = None
    return PlexService(settings=settings)


# Metadata extraction and GUID matching


@pytest.mark.parametrize(
    ("container", "titles"),
    [
        ({"Metadata": [{"title": "Season 1"}, {"title": "Season 2"}]}, ["Season 1", "Season 2"]),
        (
            {
                "SearchResult": [
                    {"score": 0.93, "Metadata": {"title": "The Rookie"}},
                    {"score": 0.85, "Metadata": {"title": "Some Movie"}},
                ]
            },
            ["The Rookie", "Some Movie"],
        ),
        ({"SearchResult": [{"Metadata": {"title": "A"}}, {"Metadata": None}]}, ["A"]),
        ({}, []),
        ({"Metadata": []}, []),
        ({"SearchResult": []}, []),
    ],
    ids=[
        "direct",
        "search_result",
        "skips_null_metadata",
        "empty",
        "empty_metadata",
        "empty_search",
    ],
)
def test_extract_metadata_items(container, titles):
    assert [i["title"] for i in PlexService._extract_metadata_items(container)] == titles


@pytest.mark.parametrize(
    ("item", "prefix", "value", "expected"),
    [
        (
            {"Guid": [{"id": "imdb://tt1"}, {"id": "tmdb://79744"}, {"id": "tvdb://350665"}]},
            "tmdb://",
            79744,
            True,
        ),
        (
            {"Guid": [{"id": "imdb://tt1"}, {"id": "tmdb://79744"}, {"id": "tvdb://350665"}]},
            "tvdb://",
            350665,
            True,
        ),
        ({"Guid": [{"id": "tmdb://79744"}]}, "tmdb://", 99999, False),
        ({"Guid": [{"id": "imdb://tt1"}]}, "tmdb://", 79744, False),
        ({"Guid": []}, "tmdb://", 79744, False),
        ({}, "tmdb://", 79744, False),
    ],
    ids=["tmdb_hit", "tvdb_hit", "wrong_id", "other_provider", "empty_guid", "no_guid_key"],
)
def test_match_guid(service, item, prefix, value, expected):
    assert service._match_guid(item, prefix, value) is expected


# Movies


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (resp({"Metadata": [movie("123", "Test Movie")]}), True),
        (resp({"Metadata": [movie("123", "Test Movie", media=False)]}), False),
        (resp({"Metadata": []}), False),
        (resp(status=400), False),
    ],
    ids=["available", "no_media_files", "not_found", "http_error"],
)
async def test_check_movie_available(service, mock_client, response, expected):
    mock_client.get.return_value = response

    assert await service.check_movie_available(12345) is expected


@pytest.mark.asyncio
async def test_get_movie_by_tmdb_returns_metadata(service, mock_client):
    mock_client.get.return_value = resp({"Metadata": [movie("456", "A Movie")]})

    result = await service.get_movie_by_tmdb(555)

    assert result is not None
    assert result["rating_key"] == "456"
    assert result["title"] == "A Movie"


@pytest.mark.asyncio
async def test_get_movie_by_tmdb_falls_back_to_section_scan(service, mock_client):
    sections = resp({"Directory": [{"key": "1", "type": "movie"}]})
    scan = resp({"Metadata": [movie("999", "Scanned Movie", Guid=[{"id": "tmdb://555"}])]})
    mock_client.get.side_effect = [resp(status=400), resp(status=400), sections, scan]

    result = await service.get_movie_by_tmdb(555)

    assert result is not None
    assert result["rating_key"] == "999"


# Shows


@pytest.mark.asyncio
@pytest.mark.parametrize("lookup", ["get_show_by_tmdb", "get_show_by_tvdb"])
async def test_get_show_search_result_format(service, mock_client, lookup):
    mock_client.get.return_value = resp(
        {
            "SearchResult": [
                {"score": 0.93, "Metadata": show("25620", "The Rookie", guid="plex://show/abc")}
            ]
        }
    )

    result = await getattr(service, lookup)(79744)

    assert result is not None
    assert result["rating_key"] == "25620"
    assert result["title"] == "The Rookie"


@pytest.mark.asyncio
async def test_get_show_by_tmdb_legacy_guid_fallback(service, mock_client):
    legacy = show("100", "Legacy Show", guid="com.plexapp.agents.themoviedb://12345?lang=en")
    mock_client.get.side_effect = [resp(), resp({"Metadata": [legacy]})]

    result = await service.get_show_by_tmdb(12345)

    assert result is not None
    assert result["rating_key"] == "100"


@pytest.mark.asyncio
async def test_get_show_by_tmdb_falls_back_to_section_scan(service, mock_client):
    sections = resp({"Directory": [{"key": "2", "type": "show"}]})
    scan = resp(
        {
            "Metadata": [
                show(
                    "25620",
                    "The Rookie",
                    Guid=[{"id": "imdb://tt7587890"}, {"id": "tmdb://79744"}],
                )
            ]
        }
    )
    mock_client.get.side_effect = [resp(status=400), resp(status=400), sections, scan]

    result = await service.get_show_by_tmdb(79744)

    assert result is not None
    assert result["rating_key"] == "25620"


@pytest.mark.asyncio
async def test_search_show_keeps_only_shows(service, mock_client):
    mock_client.get.return_value = resp(
        {
            "SearchResult": [
                {"score": 0.93, "Metadata": show("25620", "The Rookie")},
                {"score": 0.85, "Metadata": show("25736", "The Rookie: Feds")},
                {
                    "score": 0.31,
                    "Metadata": {"type": "episode", "ratingKey": "1", "title": "Pilot"},
                },
            ]
        }
    )

    result = await service.search_show("The Rookie")

    assert [r["title"] for r in result] == ["The Rookie", "The Rookie: Feds"]
    assert result[0]["rating_key"] == "25620"


# Unconfigured service


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "arg", "expected"),
    [
        ("get_movie_by_tmdb", 123, None),
        ("check_movie_available", 123, False),
        ("get_show_by_tmdb", 79744, None),
        ("get_show_by_tvdb", 350665, None),
    ],
)
async def test_unconfigured_plex_returns_empty(unconfigured_service, method, arg, expected):
    assert await getattr(unconfigured_service, method)(arg) is expected


# Authoritative miss vs inconclusive


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("second_scan", "authoritative", "failed_sections"),
    [
        (resp({"size": 0, "totalSize": 0, "Metadata": []}), True, ()),
        (httpx.RequestError("boom"), False, ("3",)),
    ],
    ids=["missing_is_authoritative", "section_failure_is_inconclusive"],
)
async def test_lookup_show_by_tvdb_miss_vs_inconclusive(
    service, mock_client, second_scan, authoritative, failed_sections
):
    sections = resp({"Directory": [{"key": "2", "type": "show"}, {"key": "3", "type": "show"}]})
    good_scan = resp({"size": 0, "totalSize": 0, "Metadata": []})
    mock_client.get.side_effect = [resp(), resp(), sections, good_scan, second_scan]

    result = await service.lookup_show_by_tvdb(777)

    assert result.item is None
    assert result.authoritative is authoritative
    assert result.failed_sections == failed_sections
