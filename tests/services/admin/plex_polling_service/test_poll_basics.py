import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from app.siftarr.models.request import RequestStatus
from tests.factories import make_request

from .helpers import episode_statuses, make_tv_request, request_status

COMPLETED = RequestStatus.COMPLETED
PENDING = RequestStatus.PENDING


@pytest.mark.parametrize(
    ("tmdb_id", "on_plex", "expected_completed", "expected_status"),
    [
        pytest.param(12345, True, 1, COMPLETED, id="available"),
        pytest.param(12345, False, 0, PENDING, id="not_available"),
        pytest.param(None, True, 0, PENDING, id="no_tmdb_id_is_skipped"),
    ],
)
async def test_poll_movie(
    db, service, mock_plex, tmdb_id, on_plex, expected_completed, expected_status
):
    req = await make_request(db, tmdb_id=tmdb_id)
    await db.commit()
    mock_plex.check_movie_available.return_value = on_plex

    completed = await service.poll(priority_only=False)

    assert completed == expected_completed
    assert await request_status(db, req) == expected_status


async def test_poll_movie_overseerr_sync_failure_does_not_block(db, service, mock_plex):
    req = await make_request(db, overseerr_request_id=123)
    await db.commit()
    mock_plex.check_movie_available.return_value = True

    with patch(
        "app.siftarr.services.admin.plex_polling_service.approve_overseerr_request_best_effort",
        new_callable=AsyncMock,
        return_value=False,
    ):
        completed = await service.poll(priority_only=False)

    assert completed == 1
    assert await request_status(db, req) == COMPLETED


@pytest.mark.parametrize(
    ("initial", "tmdb_show", "tvdb_show", "availability", "expected_completed", "expected_request"),
    [
        pytest.param(
            PENDING,
            {"rating_key": "42"},
            None,
            {(1, 1): True, (1, 2): True},
            1,
            COMPLETED,
            id="all_episodes_available",
        ),
        pytest.param(
            PENDING,
            {"rating_key": "42"},
            None,
            {(1, 1): True, (1, 2): False},
            0,
            PENDING,
            id="partial_episodes",
        ),
        pytest.param(PENDING, None, None, {}, 0, PENDING, id="show_not_in_plex"),
        pytest.param(
            PENDING,
            None,
            {"rating_key": "55"},
            {(1, 1): True, (1, 2): True},
            1,
            COMPLETED,
            id="falls_back_to_tvdb",
        ),
    ],
)
async def test_poll_tv(
    db,
    service,
    mock_plex,
    initial,
    tmdb_show,
    tvdb_show,
    availability,
    expected_completed,
    expected_request,
):
    req = await make_tv_request(db, status=initial, tvdb_id=888)
    mock_plex.get_show_by_tmdb.return_value = tmdb_show
    mock_plex.get_show_by_tvdb.return_value = tvdb_show
    mock_plex.get_episode_availability.return_value = availability

    completed = await service.poll(priority_only=False)

    assert completed == expected_completed
    assert await request_status(db, req) == expected_request
    # poll() only applies fully available shows, so partial matches leave episodes untouched.
    expected_episode = COMPLETED if expected_completed else PENDING
    assert set((await episode_statuses(db, req)).values()) == {expected_episode}


async def test_poll_one_probe_error_does_not_block_other_requests(db, service, mock_plex):
    failing = await make_request(db, external_id="1", tmdb_id=111)
    passing = await make_request(db, external_id="2", tmdb_id=222)
    await db.commit()

    async def check_movie_available(tmdb_id: int) -> bool:
        if tmdb_id == 111:
            raise Exception("boom")
        return True

    mock_plex.check_movie_available.side_effect = check_movie_available

    completed = await service.poll(priority_only=False)

    assert completed == 1
    assert await request_status(db, failing) == PENDING
    assert await request_status(db, passing) == COMPLETED


async def test_poll_caps_probe_concurrency(db, service, mock_plex):
    requests = [
        await make_request(db, external_id=str(index), tmdb_id=100 + index) for index in range(4)
    ]
    await db.commit()
    mock_plex.settings.plex_sync_concurrency = 2

    started = 0
    max_in_flight = 0
    in_flight = 0
    first_batch_ready = asyncio.Event()
    release_probes = asyncio.Event()

    async def check_movie_available(_: int) -> bool:
        nonlocal started, max_in_flight, in_flight
        started += 1
        in_flight += 1
        max_in_flight = max(max_in_flight, in_flight)
        if started == 2:
            first_batch_ready.set()
        await release_probes.wait()
        in_flight -= 1
        return True

    mock_plex.check_movie_available.side_effect = check_movie_available

    poll_task = asyncio.create_task(service.poll(priority_only=False))
    await asyncio.wait_for(first_batch_ready.wait(), timeout=1)
    await asyncio.sleep(0)

    assert started == 2
    assert max_in_flight == 2

    release_probes.set()
    completed = await poll_task

    assert completed == 4
    assert max_in_flight == 2
    assert {await request_status(db, req) for req in requests} == {COMPLETED}


async def test_poll_progress_includes_tv_detail_and_concurrency(db, service, mock_plex):
    await make_request(db, external_id="1", title="Movie", tmdb_id=111)
    await make_tv_request(db, external_id="2", title="Show", tmdb_id=222, seasons={1: 1})
    mock_plex.settings.plex_sync_concurrency = 1
    mock_plex.check_movie_available.return_value = False
    mock_plex.get_show_by_tmdb.return_value = {"rating_key": "rk"}
    mock_plex.get_episode_availability.return_value = {(1, 1): False}

    events = []

    async def collect(payload):
        events.append(payload)

    completed = await service.poll(on_progress=collect, priority_only=False)

    assert completed == 0
    details = [event for event in events if event.get("detail")]
    assert sorted(event["detail"] for event in details) == [
        "movie_lookup",
        "tv_episode_availability",
        "tv_show_lookup",
    ]
    assert all(event["concurrency_limit"] == 1 for event in events)
    assert any(
        event.get("media_type") == "tv" and event.get("title") == "Show" for event in details
    )


async def test_default_poll_only_probes_downloading_or_recently_updated_requests(
    db, service, mock_plex
):
    stale = datetime.now(UTC) - timedelta(days=3)
    recent = await make_request(db, external_id="1", tmdb_id=1)
    downloading = await make_request(
        db, external_id="2", tmdb_id=2, status=RequestStatus.DOWNLOADING, updated_at=stale
    )
    skipped = await make_request(db, external_id="3", tmdb_id=3, updated_at=stale)
    await db.commit()
    mock_plex.check_movie_available.return_value = True

    completed = await service.poll()

    assert completed == 2
    assert await request_status(db, recent) == COMPLETED
    assert await request_status(db, downloading) == COMPLETED
    assert await request_status(db, skipped) == PENDING
