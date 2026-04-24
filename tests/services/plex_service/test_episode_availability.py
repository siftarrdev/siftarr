import asyncio
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from app.siftarr.services.plex_service import PlexEpisodeAvailabilityResult, PlexTransientScanError


@pytest.fixture
def service(service_factory):
    return service_factory(concurrency=2)


@pytest.fixture
def mock_client(service, monkeypatch):
    client = AsyncMock()
    monkeypatch.setattr(service, "_get_client", AsyncMock(return_value=client))
    return client


@pytest.mark.asyncio
async def test_get_episode_availability_uses_bounded_parallel_fetches(service, monkeypatch):
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

    async def get_children(rating_key: str):
        if rating_key == "show-1":
            return seasons

        season_rating_key = rating_key
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

    monkeypatch.setattr(service._episodes, "_get_metadata_children_strict", get_children)

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

    assert availability == {(1, 1): True, (2, 2): False, (3, 3): True}
    assert max_in_flight == 2


@pytest.mark.asyncio
async def test_get_episode_availability_preserves_deterministic_filtering(service, monkeypatch):
    async def get_children(rating_key: str):
        if rating_key == "show-1":
            return [
                {"type": "season", "index": 2, "ratingKey": "season-2"},
                {"type": "artist", "index": 99, "ratingKey": "ignored"},
                {"type": "season", "ratingKey": "missing-index"},
                {"type": "season", "index": 1, "ratingKey": "season-1"},
                {"type": "season", "index": 3},
            ]

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
        }[rating_key]

    monkeypatch.setattr(service._episodes, "_get_metadata_children_strict", get_children)

    availability = await service.get_episode_availability("show-1")
    assert availability == {(2, 2): False, (2, 1): True, (1, 1): True}


@pytest.mark.asyncio
async def test_get_episode_availability_result_returns_inconclusive_on_season_failure(
    service, monkeypatch
):
    async def get_children(rating_key: str):
        if rating_key == "show-1":
            return [{"type": "season", "index": 1, "ratingKey": "season-1"}]
        raise PlexTransientScanError("network")

    monkeypatch.setattr(service._episodes, "_get_metadata_children_strict", get_children)

    result = await service.get_episode_availability_result("show-1")
    assert result == PlexEpisodeAvailabilityResult(availability={}, authoritative=False)


@pytest.mark.asyncio
async def test_get_episode_availability_result_stops_after_first_transient_failure(
    service, monkeypatch
):
    seasons = [
        {"type": "season", "index": 1, "ratingKey": "season-1"},
        {"type": "season", "index": 2, "ratingKey": "season-2"},
        {"type": "season", "index": 3, "ratingKey": "season-3"},
    ]
    started: list[str] = []
    season_1_started = asyncio.Event()
    season_2_started = asyncio.Event()
    season_3_started = asyncio.Event()
    season_1_cancelled = asyncio.Event()

    async def get_children(rating_key: str):
        if rating_key == "show-1":
            return seasons
        if rating_key == "season-1":
            started.append(rating_key)
            season_1_started.set()
            try:
                await asyncio.Future[None]()
            except asyncio.CancelledError:
                season_1_cancelled.set()
                raise
        if rating_key == "season-2":
            started.append(rating_key)
            season_2_started.set()
            raise PlexTransientScanError("network")
        if rating_key == "season-3":
            started.append(rating_key)
            season_3_started.set()
            return [{"type": "episode", "index": 1, "Media": [{"id": 3}]}]
        return []

    monkeypatch.setattr(service._episodes, "_get_metadata_children_strict", get_children)

    result = await service.get_episode_availability_result("show-1")

    await asyncio.wait_for(season_1_started.wait(), timeout=1)
    await asyncio.wait_for(season_2_started.wait(), timeout=1)
    await asyncio.wait_for(season_1_cancelled.wait(), timeout=1)

    assert result == PlexEpisodeAvailabilityResult(availability={}, authoritative=False)
    assert started == ["season-1", "season-2"]
    assert season_3_started.is_set() is False


@pytest.mark.asyncio
async def test_get_episode_availability_uses_shared_inconclusive_path(service, monkeypatch):
    async def get_result(_: str):
        return PlexEpisodeAvailabilityResult(availability={}, authoritative=False)

    monkeypatch.setattr(service._episodes, "_get_episode_availability_result", get_result)

    availability = await service.get_episode_availability("show-1")

    assert availability == {}


@pytest.mark.asyncio
async def test_get_episode_availability_result_returns_inconclusive_on_season_read_error(
    service, mock_client
):
    show_response = MagicMock()
    show_response.status_code = 200
    show_response.json.return_value = {
        "MediaContainer": {
            "Metadata": [
                {"type": "season", "index": 1, "ratingKey": "season-1"},
                {"type": "season", "index": 2, "ratingKey": "season-2"},
            ]
        }
    }
    season_response = MagicMock()
    season_response.status_code = 200
    season_response.json.return_value = {
        "MediaContainer": {"Metadata": [{"type": "episode", "index": 1, "Media": [{"id": 1}]}]}
    }
    request = httpx.Request("GET", "http://plex:32400/library/metadata/season-2/children")

    mock_client.get.side_effect = [
        show_response,
        season_response,
        httpx.ReadError("boom", request=request),
    ]

    result = await service.get_episode_availability_result("show-1")

    assert result == PlexEpisodeAvailabilityResult(availability={}, authoritative=False)


@pytest.mark.asyncio
async def test_get_episode_availability_result_fails_fast_on_season_request_error(
    service, mock_client
):
    started: list[str] = []
    season_1_started = asyncio.Event()
    season_1_cancelled = asyncio.Event()
    season_3_started = asyncio.Event()

    async def get(url: str, **kwargs):
        del kwargs
        rating_key = url.rstrip("/").split("/")[-2]
        if rating_key == "show-1":
            response = MagicMock()
            response.status_code = 200
            response.json.return_value = {
                "MediaContainer": {
                    "Metadata": [
                        {"type": "season", "index": 1, "ratingKey": "season-1"},
                        {"type": "season", "index": 2, "ratingKey": "season-2"},
                        {"type": "season", "index": 3, "ratingKey": "season-3"},
                    ]
                }
            }
            return response
        if rating_key == "season-1":
            started.append(rating_key)
            season_1_started.set()
            try:
                await asyncio.Future[None]()
            except asyncio.CancelledError:
                season_1_cancelled.set()
                raise
        if rating_key == "season-2":
            started.append(rating_key)
            raise httpx.RequestError("network", request=httpx.Request("GET", url))
        if rating_key == "season-3":
            started.append(rating_key)
            season_3_started.set()

        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {"MediaContainer": {"Metadata": []}}
        return response

    mock_client.get.side_effect = get

    result = await service.get_episode_availability_result("show-1")

    await asyncio.wait_for(season_1_started.wait(), timeout=1)
    await asyncio.wait_for(season_1_cancelled.wait(), timeout=1)

    assert result == PlexEpisodeAvailabilityResult(availability={}, authoritative=False)
    assert started == ["season-1", "season-2"]
    assert season_3_started.is_set() is False
