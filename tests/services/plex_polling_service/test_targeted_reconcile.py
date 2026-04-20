from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.siftarr.models.request import MediaType, RequestStatus

from .helpers import make_episode, make_request, make_season, set_request_status


@pytest.mark.asyncio
async def test_reconcile_request_tv_loads_request_and_reuses_episode_sync_path(
    service, mock_db, mock_plex
):
    req = make_request(
        id=77,
        media_type=MediaType.TV,
        status=RequestStatus.DOWNLOADING,
        tmdb_id=999,
        seasons=[make_season(1, [make_episode(1), make_episode(2)])],
    )
    db_result = MagicMock()
    db_result.scalar_one_or_none.return_value = req
    mock_db.execute.return_value = db_result

    mock_plex.get_show_by_tmdb.return_value = {"rating_key": "42"}
    mock_plex.get_episode_availability.return_value = {(1, 1): True, (1, 2): False}

    async def reconcile_to_partial(request, seasons, availability):
        return await set_request_status(
            request, RequestStatus.PARTIALLY_AVAILABLE, seasons, availability
        )

    with patch.object(
        service.episode_sync,
        "reconcile_existing_seasons_from_plex",
        new_callable=AsyncMock,
    ) as mock_reconcile:
        mock_reconcile.side_effect = reconcile_to_partial
        result = await service.reconcile_request(77)

    assert result.request_id == 77
    assert result.matched is True
    assert result.reconciled is True
    assert result.available is True
    assert result.status_before == RequestStatus.DOWNLOADING
    assert result.status_after == RequestStatus.PARTIALLY_AVAILABLE
    assert result.reason == "Some episodes found on Plex"
    assert result.completed_episodes == frozenset({(1, 1)})
    mock_reconcile.assert_awaited_once_with(req, req.seasons, {(1, 1): True, (1, 2): False})


@pytest.mark.asyncio
async def test_reconcile_request_tv_partial_availability(service, mock_db, mock_plex):
    req = make_request(
        id=78,
        media_type=MediaType.TV,
        status=RequestStatus.PENDING,
        tmdb_id=999,
        seasons=[make_season(1, [make_episode(1), make_episode(2)])],
    )
    db_result = MagicMock()
    db_result.scalar_one_or_none.return_value = req
    mock_db.execute.return_value = db_result

    mock_plex.get_show_by_tmdb.return_value = {"rating_key": "42"}
    mock_plex.get_episode_availability.return_value = {(1, 1): False, (1, 2): True}

    async def reconcile_to_partial(request, seasons, availability):
        return await set_request_status(
            request, RequestStatus.PARTIALLY_AVAILABLE, seasons, availability
        )

    with patch.object(
        service.episode_sync,
        "reconcile_existing_seasons_from_plex",
        new_callable=AsyncMock,
    ) as mock_reconcile:
        mock_reconcile.side_effect = reconcile_to_partial
        result = await service.reconcile_request(req)

    assert result.matched is True
    assert result.reconciled is True
    assert result.available is True
    assert result.status_before == RequestStatus.PENDING
    assert result.status_after == RequestStatus.PARTIALLY_AVAILABLE
    assert result.reason == "Some episodes found on Plex"
    assert result.requested_episode_count == 2
    assert result.completed_episodes == frozenset({(1, 2)})
    mock_reconcile.assert_awaited_once_with(req, req.seasons, {(1, 1): False, (1, 2): True})


@pytest.mark.asyncio
async def test_reconcile_request_tv_full_availability(service, mock_db, mock_plex):
    req = make_request(
        id=79,
        media_type=MediaType.TV,
        status=RequestStatus.PARTIALLY_AVAILABLE,
        tmdb_id=999,
        seasons=[make_season(1, [make_episode(1), make_episode(2)])],
    )
    db_result = MagicMock()
    db_result.scalar_one_or_none.return_value = req
    mock_db.execute.return_value = db_result

    mock_plex.get_show_by_tmdb.return_value = {"rating_key": "42"}
    mock_plex.get_episode_availability.return_value = {(1, 1): True, (1, 2): True}

    async def reconcile_to_available(request, seasons, availability):
        return await set_request_status(request, RequestStatus.AVAILABLE, seasons, availability)

    with patch.object(
        service.episode_sync,
        "reconcile_existing_seasons_from_plex",
        new_callable=AsyncMock,
    ) as mock_reconcile:
        mock_reconcile.side_effect = reconcile_to_available
        result = await service.reconcile_request(79)

    assert result.matched is True
    assert result.reconciled is True
    assert result.available is True
    assert result.status_before == RequestStatus.PARTIALLY_AVAILABLE
    assert result.status_after == RequestStatus.AVAILABLE
    assert result.reason == "All episodes found on Plex"
    assert result.requested_episode_count == 2
    assert result.completed_episodes == frozenset({(1, 1), (1, 2)})
    mock_reconcile.assert_awaited_once_with(req, req.seasons, {(1, 1): True, (1, 2): True})
