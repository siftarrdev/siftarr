from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.siftarr.models.request import MediaType, RequestStatus

from .helpers import make_episode, make_request, make_season


@pytest.mark.asyncio
async def test_check_request_movie_completes_match(service, mock_db, mock_plex):
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

    async def reconcile_to_pending(db, request, seasons, availability):
        request.status = RequestStatus.PENDING
        return seasons

    with patch(
        "app.siftarr.services.admin.plex_polling_service.persist_episode_availability",
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
    mock_reconcile.assert_awaited_once_with(
        mock_db, req, req.seasons, {(1, 1): True, (1, 2): False}
    )


@pytest.mark.asyncio
async def test_check_request_tv_full_availability(service, mock_db, mock_plex):
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

    async def reconcile_to_completed(db, request, seasons, availability):
        request.status = RequestStatus.COMPLETED
        return seasons

    with patch(
        "app.siftarr.services.admin.plex_polling_service.persist_episode_availability",
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
    mock_reconcile.assert_awaited_once_with(mock_db, req, req.seasons, {(1, 1): True, (1, 2): True})


@pytest.mark.asyncio
async def test_check_request_tv_partial_season_pack_preserves_downloading_episodes(
    service, mock_db, mock_plex
):
    s1e1 = make_episode(1, status=RequestStatus.DOWNLOADING)
    s1e2 = make_episode(2, status=RequestStatus.DOWNLOADING)
    s2e1 = make_episode(1, status=RequestStatus.DOWNLOADING)
    s2e2 = make_episode(2, status=RequestStatus.DOWNLOADING)
    req = make_request(
        id=80,
        media_type=MediaType.TV,
        status=RequestStatus.DOWNLOADING,
        tmdb_id=999,
        seasons=[make_season(1, [s1e1, s1e2]), make_season(2, [s2e1, s2e2])],
    )
    db_result = MagicMock()
    db_result.scalar_one_or_none.return_value = req
    mock_db.execute.return_value = db_result
    mock_db.flush = AsyncMock()
    mock_db.commit = AsyncMock()

    mock_plex.get_show_by_tmdb.return_value = {"rating_key": "42"}
    mock_plex.get_episode_availability.return_value = {
        (1, 1): True,
        (1, 2): True,
        (2, 1): False,
        (2, 2): False,
    }

    result = await service.check_request(80)

    assert result.matched is True
    assert result.available is True
    assert result.status_after == RequestStatus.DOWNLOADING
    assert result.reason == "Some episodes found on Plex"
    assert [s1e1.status, s1e2.status] == [RequestStatus.COMPLETED, RequestStatus.COMPLETED]
    assert [s2e1.status, s2e2.status] == [RequestStatus.DOWNLOADING, RequestStatus.DOWNLOADING]
    assert req.status == RequestStatus.DOWNLOADING
