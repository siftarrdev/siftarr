from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from app.siftarr.services.plex_service import PlexLibraryScanResult, PlexTransientScanError


@pytest.fixture
def service(service_factory):
    return service_factory(concurrency=2)


@pytest.fixture
def mock_client(service, monkeypatch):
    client = AsyncMock()
    monkeypatch.setattr(service, "_get_client", AsyncMock(return_value=client))
    return client


@pytest.mark.asyncio
async def test_iter_full_library_items_uses_pagination(service, mock_client):
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
                {"type": "movie", "ratingKey": "101", "title": "One", "Guid": [{"id": "tmdb://1"}]},
                {"type": "movie", "ratingKey": "102", "title": "Two", "Guid": [{"id": "tmdb://2"}]},
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
async def test_iter_recently_added_items_uses_recently_added_endpoint(service, mock_client):
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
async def test_scan_cycle_caches_section_listing_and_lookup_results(service, mock_client):
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
    mock_client.get.side_effect = [empty_search, empty_search, sections_response, section_scan]

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
async def test_iter_section_items_raises_transient_error_on_http_failure(service, mock_client):
    mock_client.get.side_effect = httpx.RequestError("network")

    with pytest.raises(PlexTransientScanError):
        [item async for item in service.iter_section_items("5")]


@pytest.mark.asyncio
async def test_scan_library_items_reports_partial_failure_authoritatively(service, monkeypatch):
    async def get_sections(media_type: str, *, strict: bool):
        assert media_type == "movie"
        assert strict is True
        return [{"key": "1", "type": "movie"}, {"key": "2", "type": "movie"}]

    async def iter_section_items(section_key: str, **kwargs):
        del kwargs
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
