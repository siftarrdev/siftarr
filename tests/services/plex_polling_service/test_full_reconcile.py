from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.siftarr.models.request import MediaType, RequestStatus

from .helpers import make_episode, make_request, make_season


@pytest.mark.asyncio
async def test_check_request_movie_completes_match(
    service, mock_db, mock_plex
):
    req = make_request(id=1, tmdb_id=111, title="Movie A")
    db_result = MagicMock()
    db_result.scalar_one_or_none.return_value = req
    mock_db.execute.return_value = db_result
    mock_plex.check_movie_available.return_value = True

    with patch.object(service.lifecycle, "transition", new_callable=AsyncMock) as mock_transition:
        result = await service.check_request(1)

    assert result.request_id == 1
    assert result.matched is True
    assert result.available is True
    mock_transition.assert_awaited_once_with(1, RequestStatus.COMPLETED, reason="Found on Plex")


@pytest.mark.asyncio
async def test_check_request_tv_loads_request_and_reuses_episode_sync_path(
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

    async def reconcile_to_pending(request, seasons, availability):
        request.status = RequestStatus.PENDING
        return seasons

    with patch.object(
        service.episode_sync,
        "reconcile_existing_seasons_from_plex",
        new_callable=AsyncMock,
    ) as mock_reconcile:
        mock_reconcile.side_effect = reconcile_to_pending
        result = await service.check_request(77)

    assert result.request_id == 77
    assert result.matched is True
    assert result.available is True
    assert result.status_before == RequestStatus.DOWNLOADING
    assert result.status_after == RequestStatus.PENDING
    assert result.reason == "Some episodes found on Plex"
    mock_reconcile.assert_awaited_once_with(req, req.seasons, {(1, 1): True, (1, 2): False})


@pytest.mark.asyncio
async def test_check_request_tv_full_availability(
    service, mock_db, mock_plex
):
    req = make_request(
        id=79,
        media_type=MediaType.TV,
        status=RequestStatus.PENDING,
        tmdb_id=999,
        seasons=[make_season(1, [make_episode(1), make_episode(2)])],
    )
    db_result = MagicMock()
    db_result.scalar_one_or_none.return_value = req
    mock_db.execute.return_value = db_result

    mock_plex.get_show_by_tmdb.return_value = {"rating_key": "42"}
    mock_plex.get_episode_availability.return_value = {(1, 1): True, (1, 2): True}

    async def reconcile_to_completed(request, seasons, availability):
        request.status = RequestStatus.COMPLETED
        return seasons

    with patch.object(
        service.episode_sync,
        "reconcile_existing_seasons_from_plex",
        new_callable=AsyncMock,
    ) as mock_reconcile:
        mock_reconcile.side_effect = reconcile_to_completed
        result = await service.check_request(79)

    assert result.request_id == 79
    assert result.matched is True
    assert result.available is True
    assert result.status_before == RequestStatus.PENDING
    assert result.status_after == RequestStatus.COMPLETED
    assert result.reason == "All episodes found on Plex"
    mock_reconcile.assert_awaited_once_with(req, req.seasons, {(1, 1): True, (1, 2): True})
