import asyncio

import pytest

from app.siftarr.services.plex_service import PlexEpisodeAvailabilityResult, PlexTransientScanError


@pytest.fixture
def service(service_factory):
    return service_factory(concurrency=2)


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

    assert availability == {(1, 1): True, (2, 2): False, (3, 3): True}
    assert max_in_flight == 2


@pytest.mark.asyncio
async def test_get_episode_availability_preserves_deterministic_filtering(service, monkeypatch):
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
    assert availability == {(2, 2): False, (2, 1): True, (1, 1): True}


@pytest.mark.asyncio
async def test_get_episode_availability_result_returns_inconclusive_on_season_failure(
    service, monkeypatch
):
    async def get_children(rating_key: str):
        if rating_key == "show-1":
            return [{"type": "season", "index": 1, "ratingKey": "season-1"}]
        raise PlexTransientScanError("network")

    monkeypatch.setattr(service, "_get_metadata_children_strict", get_children)

    result = await service.get_episode_availability_result("show-1")
    assert result == PlexEpisodeAvailabilityResult(availability={}, authoritative=False)
