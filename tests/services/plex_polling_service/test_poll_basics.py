import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.siftarr.models.request import MediaType, RequestStatus

from .helpers import make_episode, make_request, make_season, set_request_status


@pytest.mark.asyncio
async def test_poll_no_active_requests(service, mock_db):
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []
    mock_db.execute.return_value = mock_result

    completed = await service.poll()
    assert completed == 0


@pytest.mark.asyncio
async def test_poll_movie_available(service, mock_db, mock_plex):
    req = make_request()
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [req]
    mock_db.execute.return_value = mock_result
    mock_plex.check_movie_available.return_value = True

    with patch.object(service.lifecycle, "transition", new_callable=AsyncMock) as mock_transition:
        mock_transition.return_value = req
        completed = await service.poll()

    assert completed == 1
    mock_plex.check_movie_available.assert_called_once_with(12345)
    mock_transition.assert_called_once_with(1, RequestStatus.COMPLETED, reason="Found on Plex")


@pytest.mark.asyncio
async def test_poll_movie_not_available(service, mock_db, mock_plex):
    req = make_request()
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [req]
    mock_db.execute.return_value = mock_result
    mock_plex.check_movie_available.return_value = False

    with patch.object(service.lifecycle, "transition", new_callable=AsyncMock) as mock_transition:
        completed = await service.poll()

    assert completed == 0
    mock_transition.assert_not_called()


@pytest.mark.asyncio
async def test_poll_movie_no_tmdb_id(service, mock_db, mock_plex):
    req = make_request(tmdb_id=None)
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [req]
    mock_db.execute.return_value = mock_result

    completed = await service.poll()
    assert completed == 0
    mock_plex.check_movie_available.assert_not_called()


@pytest.mark.asyncio
async def test_poll_tv_all_episodes_available(service, mock_db, mock_plex):
    ep1 = make_episode(1)
    ep2 = make_episode(2)
    season = make_season(1, [ep1, ep2])
    req = make_request(media_type=MediaType.TV, tmdb_id=999, seasons=[season])
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [req]
    mock_db.execute.return_value = mock_result

    mock_plex.get_show_by_tmdb.return_value = {"rating_key": "42"}
    mock_plex.get_episode_availability.return_value = {(1, 1): True, (1, 2): True}

    async def reconcile_to_available(request, seasons, availability):
        return await set_request_status(request, RequestStatus.AVAILABLE, seasons, availability)

    with (
        patch.object(
            service.episode_sync,
            "reconcile_existing_seasons_from_plex",
            new_callable=AsyncMock,
        ) as mock_reconcile,
        patch.object(service.lifecycle, "transition", new_callable=AsyncMock) as mock_transition,
    ):
        mock_reconcile.side_effect = reconcile_to_available
        completed = await service.poll()

    assert completed == 1
    mock_reconcile.assert_awaited_once_with(req, req.seasons, {(1, 1): True, (1, 2): True})
    mock_transition.assert_not_awaited()


@pytest.mark.asyncio
async def test_poll_tv_partial_episodes(service, mock_db, mock_plex):
    ep1 = make_episode(1)
    ep2 = make_episode(2)
    season = make_season(1, [ep1, ep2])
    req = make_request(media_type=MediaType.TV, tmdb_id=999, seasons=[season])
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [req]
    mock_db.execute.return_value = mock_result

    mock_plex.get_show_by_tmdb.return_value = {"rating_key": "42"}
    mock_plex.get_episode_availability.return_value = {(1, 1): True, (1, 2): False}

    with patch.object(service.lifecycle, "transition", new_callable=AsyncMock) as mock_transition:
        completed = await service.poll()

    assert completed == 0
    mock_transition.assert_not_called()


@pytest.mark.asyncio
async def test_poll_partially_available_request_can_complete(service, mock_db, mock_plex):
    ep1 = make_episode(1, status=RequestStatus.COMPLETED)
    ep2 = make_episode(2, status=RequestStatus.PENDING)
    season = make_season(1, [ep1, ep2])
    req = make_request(
        media_type=MediaType.TV,
        status=RequestStatus.PARTIALLY_AVAILABLE,
        tmdb_id=999,
        seasons=[season],
    )
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [req]
    mock_db.execute.return_value = mock_result

    mock_plex.get_show_by_tmdb.return_value = {"rating_key": "42"}
    mock_plex.get_episode_availability.return_value = {(1, 1): True, (1, 2): True}

    async def reconcile_to_available(request, seasons, availability):
        return await set_request_status(request, RequestStatus.AVAILABLE, seasons, availability)

    with (
        patch.object(
            service.episode_sync,
            "reconcile_existing_seasons_from_plex",
            new_callable=AsyncMock,
        ) as mock_reconcile,
        patch.object(service.lifecycle, "transition", new_callable=AsyncMock) as mock_transition,
    ):
        mock_reconcile.side_effect = reconcile_to_available
        completed = await service.poll()

    assert completed == 1
    mock_reconcile.assert_awaited_once_with(req, req.seasons, {(1, 1): True, (1, 2): True})
    mock_transition.assert_not_awaited()


@pytest.mark.asyncio
async def test_poll_tv_show_not_in_plex(service, mock_db, mock_plex):
    req = make_request(
        media_type=MediaType.TV,
        tmdb_id=999,
        tvdb_id=None,
        seasons=[make_season(1, [make_episode(1)])],
    )
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [req]
    mock_db.execute.return_value = mock_result
    mock_plex.get_show_by_tmdb.return_value = None

    completed = await service.poll()
    assert completed == 0


@pytest.mark.asyncio
async def test_poll_tv_fallback_to_tvdb(service, mock_db, mock_plex):
    req = make_request(
        media_type=MediaType.TV,
        tmdb_id=999,
        tvdb_id=888,
        seasons=[make_season(1, [make_episode(1)])],
    )
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [req]
    mock_db.execute.return_value = mock_result

    mock_plex.get_show_by_tmdb.return_value = None
    mock_plex.get_show_by_tvdb.return_value = {"rating_key": "55"}
    mock_plex.get_episode_availability.return_value = {(1, 1): True}

    async def reconcile_to_available(request, seasons, availability):
        return await set_request_status(request, RequestStatus.AVAILABLE, seasons, availability)

    with (
        patch.object(
            service.episode_sync,
            "reconcile_existing_seasons_from_plex",
            new_callable=AsyncMock,
        ) as mock_reconcile,
        patch.object(service.lifecycle, "transition", new_callable=AsyncMock) as mock_transition,
    ):
        mock_reconcile.side_effect = reconcile_to_available
        completed = await service.poll()

    assert completed == 1
    mock_plex.get_show_by_tvdb.assert_called_once_with(888)
    mock_reconcile.assert_awaited_once_with(req, req.seasons, {(1, 1): True})
    mock_transition.assert_not_awaited()


@pytest.mark.asyncio
async def test_poll_error_handling(service, mock_db, mock_plex):
    req1 = make_request(id=1, tmdb_id=111)
    req2 = make_request(id=2, tmdb_id=222)
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [req1, req2]
    mock_db.execute.return_value = mock_result
    mock_plex.check_movie_available.side_effect = [Exception("boom"), True]

    with patch.object(service.lifecycle, "transition", new_callable=AsyncMock) as mock_transition:
        mock_transition.return_value = req2
        completed = await service.poll()

    assert completed == 1


@pytest.mark.asyncio
async def test_poll_caps_probe_concurrency(service, mock_db, mock_plex):
    requests = [make_request(id=index, tmdb_id=100 + index) for index in range(1, 5)]
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = requests
    mock_db.execute.return_value = mock_result
    mock_plex.settings.plex_sync_concurrency = 2

    started = 0
    max_in_flight = 0
    in_flight = 0
    first_batch_ready = asyncio.Event()
    third_probe_started = asyncio.Event()
    release_probes = asyncio.Event()
    state_lock = asyncio.Lock()

    async def check_movie_available(_: int) -> bool:
        nonlocal started, max_in_flight, in_flight
        async with state_lock:
            started += 1
            in_flight += 1
            max_in_flight = max(max_in_flight, in_flight)
            if started == 2:
                first_batch_ready.set()
            if started == 3:
                third_probe_started.set()
        await release_probes.wait()
        async with state_lock:
            in_flight -= 1
        return True

    mock_plex.check_movie_available.side_effect = check_movie_available

    with patch.object(service.lifecycle, "transition", new_callable=AsyncMock) as mock_transition:
        poll_task = asyncio.create_task(service.poll())
        await asyncio.wait_for(first_batch_ready.wait(), timeout=1)

        assert started == 2
        assert max_in_flight == 2
        assert third_probe_started.is_set() is False
        mock_transition.assert_not_called()

        release_probes.set()
        completed = await poll_task

    assert completed == 4
    assert max_in_flight == 2


@pytest.mark.asyncio
async def test_update_episode_statuses(service, mock_db):
    req = make_request(media_type=MediaType.TV, seasons=[make_season(1, [make_episode(1), make_episode(2)])])

    completed_episodes = frozenset({(1, 1), (1, 2)})
    await service._update_episode_statuses(req, completed_episodes)

    assert req.seasons[0].episodes[0].status == RequestStatus.COMPLETED
    assert req.seasons[0].episodes[1].status == RequestStatus.COMPLETED
    assert req.seasons[0].status == RequestStatus.COMPLETED
    mock_db.commit.assert_called_once()
