from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.siftarr.models.request import MediaType, RequestStatus

from .helpers import make_episode, make_request, make_season


@pytest.mark.asyncio
async def test_full_reconcile_scan_builds_authoritative_presence_and_completes_matches(
    service, mock_db, mock_plex
):
    movie_req = make_request(id=1, tmdb_id=111, title="Movie A")
    tv_req = make_request(
        id=2,
        media_type=MediaType.TV,
        status=RequestStatus.PENDING,
        tmdb_id=222,
        title="Show B",
        seasons=[make_season(1, [make_episode(1), make_episode(2)])],
    )
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [movie_req, tv_req]
    mock_db.execute.return_value = mock_result

    mock_plex.scan_library_items = AsyncMock(
        side_effect=[
            SimpleNamespace(
                items=(
                    (
                        {
                            "type": "movie",
                            "rating_key": "movie-111",
                            "guids": ("tmdb://111",),
                            "Media": [{"id": 1}],
                        },
                    )
                ),
                authoritative=True,
                failed_sections=(),
            ),
            SimpleNamespace(
                items=(
                    (
                        {
                            "type": "show",
                            "rating_key": "show-222",
                            "guids": ("tmdb://222",),
                            "Media": [{"id": 1}],
                        },
                    )
                ),
                authoritative=True,
                failed_sections=(),
            ),
        ]
    )
    mock_plex.get_episode_availability_result = AsyncMock(
        return_value=SimpleNamespace(authoritative=True, availability={(1, 1): True, (1, 2): True})
    )

    async def reconcile_existing_seasons_from_plex(req, seasons, availability):
        assert req is tv_req
        assert seasons == tv_req.seasons
        assert availability == {(1, 1): True, (1, 2): True}
        req.status = RequestStatus.COMPLETED
        return seasons

    service.episode_sync.reconcile_existing_seasons_from_plex = AsyncMock(
        side_effect=reconcile_existing_seasons_from_plex
    )

    with patch.object(service.lifecycle, "transition", new_callable=AsyncMock) as mock_transition:
        result = await service.full_reconcile_scan()

    assert result.completed_requests == 2
    assert result.metrics.scanned_items == 2
    assert result.metrics.matched_requests == 2
    assert result.metrics.deduped_items == 0
    assert result.metrics.downgraded_requests == 0
    assert result.metrics.skipped_on_error_items == 0
    service.episode_sync.reconcile_existing_seasons_from_plex.assert_awaited_once()
    mock_transition.assert_awaited_once_with(1, RequestStatus.COMPLETED, reason="Found on Plex")


@pytest.mark.asyncio
async def test_full_reconcile_scan_downgrades_stale_movie_on_clean_authoritative_miss(
    service, mock_db, mock_plex
):
    req = make_request(id=1, tmdb_id=111, title="Movie A", status=RequestStatus.COMPLETED)
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [req]
    mock_db.execute.return_value = mock_result

    mock_plex.scan_library_items = AsyncMock(
        side_effect=[
            SimpleNamespace(items=(), authoritative=True, failed_sections=()),
            SimpleNamespace(items=(), authoritative=True, failed_sections=()),
        ]
    )
    mock_plex.get_episode_availability_result = AsyncMock()

    with patch.object(service.lifecycle, "transition", new_callable=AsyncMock) as mock_transition:
        result = await service.full_reconcile_scan()

    assert result.completed_requests == 0
    assert result.metrics.matched_requests == 0
    assert result.metrics.downgraded_requests == 1
    assert result.metrics.skipped_on_error_items == 0
    mock_transition.assert_awaited_once_with(
        1,
        RequestStatus.PENDING,
        reason="Full Plex reconcile no longer finds this movie",
    )


@pytest.mark.asyncio
async def test_full_reconcile_scan_keeps_stale_movie_untouched_when_scan_non_authoritative(
    service, mock_db, mock_plex
):
    req = make_request(id=1, tmdb_id=111, title="Movie A", status=RequestStatus.COMPLETED)
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [req]
    mock_db.execute.return_value = mock_result

    mock_plex.scan_library_items = AsyncMock(
        side_effect=[
            SimpleNamespace(items=(), authoritative=False, failed_sections=("1",)),
            SimpleNamespace(items=(), authoritative=True, failed_sections=()),
        ]
    )
    mock_plex.get_episode_availability_result = AsyncMock()

    with patch.object(service.lifecycle, "transition", new_callable=AsyncMock) as mock_transition:
        result = await service.full_reconcile_scan()

    assert result.completed_requests == 0
    assert result.metrics.downgraded_requests == 0
    assert result.metrics.skipped_on_error_items == 1
    mock_transition.assert_not_awaited()


@pytest.mark.asyncio
async def test_full_reconcile_scan_recomputes_tv_status_via_episode_sync_on_authoritative_miss(
    service, mock_db, mock_plex
):
    req = make_request(
        id=2,
        media_type=MediaType.TV,
        status=RequestStatus.COMPLETED,
        tmdb_id=222,
        title="Show B",
        seasons=[make_season(1, [make_episode(1), make_episode(2)])],
    )
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [req]
    mock_db.execute.return_value = mock_result

    mock_plex.scan_library_items = AsyncMock(
        side_effect=[
            SimpleNamespace(items=(), authoritative=True, failed_sections=()),
            SimpleNamespace(items=(), authoritative=True, failed_sections=()),
        ]
    )

    async def reconcile_existing_seasons_from_plex(request, seasons, availability):
        assert request is req
        assert seasons == req.seasons
        assert availability == {}
        request.status = RequestStatus.PENDING
        return seasons

    service.episode_sync.reconcile_existing_seasons_from_plex = AsyncMock(
        side_effect=reconcile_existing_seasons_from_plex
    )

    with patch.object(service.lifecycle, "transition", new_callable=AsyncMock) as mock_transition:
        result = await service.full_reconcile_scan()

    assert result.completed_requests == 0
    assert result.metrics.matched_requests == 0
    assert result.metrics.downgraded_requests == 1
    assert result.metrics.skipped_on_error_items == 0
    service.episode_sync.reconcile_existing_seasons_from_plex.assert_awaited_once_with(
        req,
        req.seasons,
        {},
    )
    mock_transition.assert_not_awaited()


@pytest.mark.asyncio
async def test_full_reconcile_scan_skips_tv_negative_sync_when_episode_scan_fails_transiently(
    service, mock_db, mock_plex
):
    req = make_request(
        id=2,
        media_type=MediaType.TV,
        status=RequestStatus.PENDING,
        tmdb_id=222,
        title="Show B",
        seasons=[make_season(1, [make_episode(1)])],
    )
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [req]
    mock_db.execute.return_value = mock_result

    mock_plex.scan_library_items = AsyncMock(
        side_effect=[
            SimpleNamespace(items=(), authoritative=True, failed_sections=()),
            SimpleNamespace(
                items=(
                    {
                        "type": "show",
                        "rating_key": "show-222",
                        "guids": ("tmdb://222",),
                        "Media": [{"id": 1}],
                    },
                ),
                authoritative=True,
                failed_sections=(),
            ),
        ]
    )
    mock_plex.get_episode_availability_result = AsyncMock(
        return_value=SimpleNamespace(authoritative=False, availability={})
    )
    service.episode_sync.reconcile_existing_seasons_from_plex = AsyncMock()

    with patch.object(service.lifecycle, "transition", new_callable=AsyncMock) as mock_transition:
        result = await service.full_reconcile_scan()

    assert result.completed_requests == 0
    assert result.metrics.matched_requests == 1
    assert result.metrics.downgraded_requests == 0
    assert result.metrics.skipped_on_error_items == 1
    service.episode_sync.reconcile_existing_seasons_from_plex.assert_not_called()
    mock_transition.assert_not_awaited()


@pytest.mark.asyncio
async def test_full_reconcile_scan_counts_deduped_presence_items_without_double_downgrading(
    service, mock_db, mock_plex
):
    req = make_request(id=1, tmdb_id=111, title="Movie A", status=RequestStatus.COMPLETED)
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [req]
    mock_db.execute.return_value = mock_result

    duplicate_movie = {
        "type": "movie",
        "rating_key": "movie-111",
        "guids": ("tmdb://111",),
        "Media": [{"id": 1}],
    }
    mock_plex.scan_library_items = AsyncMock(
        side_effect=[
            SimpleNamespace(
                items=(duplicate_movie, dict(duplicate_movie)),
                authoritative=True,
                failed_sections=(),
            ),
            SimpleNamespace(items=(), authoritative=True, failed_sections=()),
        ]
    )
    mock_plex.get_episode_availability_result = AsyncMock()

    with patch.object(service.lifecycle, "transition", new_callable=AsyncMock) as mock_transition:
        result = await service.full_reconcile_scan()

    assert result.completed_requests == 0
    assert result.metrics.scanned_items == 2
    assert result.metrics.matched_requests == 1
    assert result.metrics.deduped_items == 1
    assert result.metrics.downgraded_requests == 0
    mock_transition.assert_not_awaited()
