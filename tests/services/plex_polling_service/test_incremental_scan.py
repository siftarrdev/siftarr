import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.siftarr.models.request import MediaType, RequestStatus
from app.siftarr.services.plex_service import (
    PlexEpisodeAvailabilityResult,
    PlexLookupResult,
    PlexTransientScanError,
)

from .helpers import make_episode, make_request, make_season, set_request_status


@pytest.mark.asyncio
async def test_incremental_recent_scan_applies_completion_updates_after_probe_phase(
    service, mock_db, mock_plex
):
    req1 = make_request(
        id=1,
        media_type=MediaType.TV,
        tmdb_id=901,
        title="Show 1",
        seasons=[make_season(1, [make_episode(1), make_episode(2)])],
    )
    req2 = make_request(
        id=2,
        media_type=MediaType.TV,
        tmdb_id=902,
        title="Show 2",
        seasons=[make_season(1, [make_episode(1), make_episode(2)])],
    )
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [req1, req2]
    mock_db.execute.return_value = mock_result
    mock_plex.settings.plex_sync_concurrency = 2

    async def iter_recently_added_items(media_type: str):
        if media_type == "show":
            yield {
                "type": "show",
                "rating_key": "show-1",
                "title": "Show 1",
                "added_at": int(datetime(2026, 4, 19, 12, 0, tzinfo=UTC).timestamp()),
                "guids": ("tmdb://901",),
                "Media": [{"id": 1}],
            }
            yield {
                "type": "show",
                "rating_key": "show-2",
                "title": "Show 2",
                "added_at": int(datetime(2026, 4, 19, 12, 1, tzinfo=UTC).timestamp()),
                "guids": ("tmdb://902",),
                "Media": [{"id": 1}],
            }
        if False:
            yield {}

    mock_plex.iter_recently_added_items = iter_recently_added_items

    probes_started = 0
    probes_finished = 0
    active_writes = 0
    max_active_writes = 0
    probes_ready = asyncio.Event()
    release_probes = asyncio.Event()
    first_write_started = asyncio.Event()
    second_write_started = asyncio.Event()
    release_writes = asyncio.Event()
    state_lock = asyncio.Lock()

    async def get_episode_availability_result(_: str) -> PlexEpisodeAvailabilityResult:
        nonlocal probes_started, probes_finished
        async with state_lock:
            probes_started += 1
            if probes_started == 2:
                probes_ready.set()
        await release_probes.wait()
        async with state_lock:
            probes_finished += 1
        return PlexEpisodeAvailabilityResult(
            availability={(1, 1): True, (1, 2): True},
            authoritative=True,
        )

    reconcile_calls = 0

    async def reconcile_existing_seasons_from_plex(req, seasons, availability) -> None:
        nonlocal reconcile_calls, active_writes, max_active_writes
        assert probes_finished == 2
        assert seasons == req.seasons
        assert availability == {(1, 1): True, (1, 2): True}
        reconcile_calls += 1
        if reconcile_calls == 1:
            first_write_started.set()
        else:
            second_write_started.set()
        active_writes += 1
        max_active_writes = max(max_active_writes, active_writes)
        await release_writes.wait()
        active_writes -= 1

    mock_plex.get_episode_availability_result.side_effect = get_episode_availability_result

    with (
        patch.object(
            service.episode_sync,
            "reconcile_existing_seasons_from_plex",
            side_effect=reconcile_existing_seasons_from_plex,
        ) as mock_reconcile,
        patch.object(service.lifecycle, "transition", new_callable=AsyncMock) as mock_transition,
    ):
        scan_task = asyncio.create_task(service.incremental_recent_scan())
        await asyncio.wait_for(probes_ready.wait(), timeout=1)

        assert probes_finished == 0
        mock_reconcile.assert_not_called()
        mock_transition.assert_not_called()

        release_probes.set()
        await asyncio.wait_for(first_write_started.wait(), timeout=1)

        assert second_write_started.is_set() is False

        release_writes.set()
        result = await scan_task

    assert result.completed_requests == 2
    assert result.metrics.scanned_items == 2
    assert result.metrics.matched_requests == 2
    assert result.metrics.deduped_items == 0
    assert result.metrics.skipped_on_error_items == 0
    assert max_active_writes == 1
    assert mock_reconcile.await_count == 2
    assert mock_transition.await_count == 0


@pytest.mark.asyncio
async def test_incremental_recent_scan_collapses_duplicate_media_ids_within_cycle(
    service, mock_db, mock_plex
):
    req1 = make_request(id=1, tmdb_id=111, title="Movie A")
    req2 = make_request(id=2, tmdb_id=111, title="Movie A Duplicate")
    req3 = make_request(id=3, tmdb_id=222, title="Movie B")
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [req1, req2, req3]
    mock_db.execute.return_value = mock_result

    async def iter_recently_added_items(media_type: str):
        if media_type == "movie":
            yield {
                "type": "movie",
                "rating_key": "movie-111",
                "title": "Movie A",
                "added_at": int(datetime(2026, 4, 19, 12, 0, tzinfo=UTC).timestamp()),
                "guids": ("tmdb://111",),
                "Media": [{"id": 1}],
            }
            yield {
                "type": "movie",
                "rating_key": "movie-222",
                "title": "Movie B",
                "added_at": int(datetime(2026, 4, 19, 12, 1, tzinfo=UTC).timestamp()),
                "guids": ("tmdb://222",),
                "Media": [],
            }
        if False:
            yield {}

    mock_plex.iter_recently_added_items = iter_recently_added_items
    mock_plex.lookup_movie_by_tmdb = AsyncMock(
        return_value=PlexLookupResult(item=None, authoritative=True)
    )

    with patch.object(service.lifecycle, "transition", new_callable=AsyncMock) as mock_transition:
        result = await service.incremental_recent_scan()

    assert result.completed_requests == 2
    assert result.metrics.scanned_items == 2
    assert result.metrics.matched_requests == 2
    assert result.metrics.deduped_items == 1
    assert result.metrics.skipped_on_error_items == 0
    mock_plex.lookup_movie_by_tmdb.assert_awaited_once_with(222)
    mock_plex.check_movie_available.assert_not_called()
    mock_transition.assert_any_call(1, RequestStatus.COMPLETED, reason="Found on Plex")
    mock_transition.assert_any_call(2, RequestStatus.COMPLETED, reason="Found on Plex")
    assert mock_transition.await_count == 2


@pytest.mark.asyncio
async def test_incremental_recent_scan_processes_recent_matches_and_skips_unmatched_items(
    service, mock_db, mock_plex
):
    req1 = make_request(id=1, tmdb_id=111, title="Movie A")
    req2 = make_request(
        id=2,
        media_type=MediaType.TV,
        tmdb_id=222,
        title="Show B",
        seasons=[make_season(1, [make_episode(1)])],
    )
    req3 = make_request(id=3, tmdb_id=999, title="Movie C")
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [req1, req2, req3]
    mock_db.execute.return_value = mock_result

    async def iter_recently_added_items(media_type: str):
        if media_type == "movie":
            yield {
                "type": "movie",
                "rating_key": "movie-111",
                "title": "Movie A",
                "added_at": int(datetime(2026, 4, 19, 11, 55, tzinfo=UTC).timestamp()),
                "guids": ("tmdb://111",),
                "Media": [{"id": 1}],
            }
            yield {
                "type": "movie",
                "rating_key": "movie-old",
                "title": "Unmatched",
                "added_at": int(datetime(2026, 4, 19, 11, 49, tzinfo=UTC).timestamp()),
                "guids": ("tmdb://555",),
                "Media": [{"id": 1}],
            }
            return
        yield {
            "type": "show",
            "rating_key": "show-222",
            "title": "Show B",
            "added_at": int(datetime(2026, 4, 19, 12, 1, tzinfo=UTC).timestamp()),
            "guids": ("tmdb://222",),
            "Media": [{"id": 1}],
        }

    mock_plex.iter_recently_added_items = iter_recently_added_items
    mock_plex.get_episode_availability_result = AsyncMock(
        return_value=PlexEpisodeAvailabilityResult(
            availability={(1, 1): True},
            authoritative=True,
        )
    )

    async def reconcile_to_completed(request, seasons, availability):
        return await set_request_status(request, RequestStatus.COMPLETED, seasons, availability)

    with (
        patch.object(
            service.episode_sync,
            "reconcile_existing_seasons_from_plex",
            new_callable=AsyncMock,
        ) as mock_reconcile,
        patch.object(service.lifecycle, "transition", new_callable=AsyncMock) as mock_transition,
    ):
        mock_reconcile.side_effect = reconcile_to_completed
        result = await service.incremental_recent_scan()

    assert result.completed_requests == 2
    assert result.metrics.scanned_items == 3
    assert result.metrics.matched_requests == 2
    assert result.metrics.skipped_on_error_items == 0
    mock_plex.check_movie_available.assert_not_called()
    mock_plex.get_episode_availability_result.assert_awaited_once_with("show-222")
    mock_transition.assert_awaited_once_with(1, RequestStatus.COMPLETED, reason="Found on Plex")
    mock_reconcile.assert_awaited_once_with(req2, req2.seasons, {(1, 1): True})


@pytest.mark.asyncio
async def test_incremental_recent_scan_reuses_recent_item_data_before_targeted_fallback(
    service, mock_db, mock_plex
):
    movie_req = make_request(id=1, tmdb_id=111, title="Movie A")
    tv_req = make_request(
        id=2,
        media_type=MediaType.TV,
        tmdb_id=222,
        title="Show B",
        seasons=[make_season(1, [make_episode(1)])],
    )
    movie_fallback_req = make_request(id=3, tmdb_id=333, title="Movie C")
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [movie_req, tv_req, movie_fallback_req]
    mock_db.execute.return_value = mock_result

    async def iter_recently_added_items(media_type: str):
        if media_type == "movie":
            yield {
                "type": "movie",
                "rating_key": "movie-111",
                "title": "Movie A",
                "added_at": int(datetime(2026, 4, 19, 12, 0, tzinfo=UTC).timestamp()),
                "guids": ("tmdb://111",),
                "Media": [{"id": 1}],
            }
            yield {
                "type": "movie",
                "rating_key": "movie-333",
                "title": "Movie C",
                "added_at": int(datetime(2026, 4, 19, 12, 1, tzinfo=UTC).timestamp()),
                "guids": ("tmdb://333",),
                "Media": [],
            }
            return
        yield {
            "type": "show",
            "rating_key": "show-222",
            "title": "Show B",
            "added_at": int(datetime(2026, 4, 19, 12, 2, tzinfo=UTC).timestamp()),
            "guids": ("tmdb://222",),
            "Media": [{"id": 1}],
        }

    mock_plex.iter_recently_added_items = iter_recently_added_items
    mock_plex.lookup_movie_by_tmdb = AsyncMock(
        return_value=PlexLookupResult(
            item={"rating_key": "movie-333", "Media": [{"id": 1}]},
            authoritative=True,
        )
    )
    mock_plex.get_episode_availability_result = AsyncMock(
        return_value=PlexEpisodeAvailabilityResult(
            availability={(1, 1): True},
            authoritative=True,
        )
    )

    async def reconcile_to_completed(request, seasons, availability):
        return await set_request_status(request, RequestStatus.COMPLETED, seasons, availability)

    with (
        patch.object(
            service.episode_sync,
            "reconcile_existing_seasons_from_plex",
            new_callable=AsyncMock,
        ) as mock_reconcile,
        patch.object(service.lifecycle, "transition", new_callable=AsyncMock) as mock_transition,
    ):
        mock_reconcile.side_effect = reconcile_to_completed
        result = await service.incremental_recent_scan()

    assert result.completed_requests == 3
    mock_plex.lookup_movie_by_tmdb.assert_awaited_once_with(333)
    mock_plex.check_movie_available.assert_not_called()
    mock_plex.get_show_by_tmdb.assert_not_called()
    mock_plex.get_show_by_tvdb.assert_not_called()
    mock_plex.get_episode_availability_result.assert_awaited_once_with("show-222")
    mock_plex.get_episode_availability.assert_not_called()
    assert mock_transition.await_count == 2
    mock_reconcile.assert_awaited_once_with(tv_req, tv_req.seasons, {(1, 1): True})


@pytest.mark.asyncio
async def test_incremental_recent_scan_reports_recent_scan_failure(service, mock_db, mock_plex):
    req = make_request(id=1, tmdb_id=111, title="Movie A")
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [req]
    mock_db.execute.return_value = mock_result

    async def iter_recently_added_items(media_type: str):
        if media_type == "movie":
            raise PlexTransientScanError("recently added unavailable")
        if False:
            yield {}

    mock_plex.iter_recently_added_items = iter_recently_added_items

    result = await service.incremental_recent_scan()

    assert result.completed_requests == 0
    assert result.metrics.scanned_items == 0
    assert result.metrics.skipped_on_error_items == 1
    assert result.clean_run is False
    assert result.last_error == "recently added unavailable"


@pytest.mark.asyncio
async def test_incremental_recent_scan_reports_request_probe_error(service, mock_db, mock_plex):
    req = make_request(id=1, tmdb_id=111, title="Movie A")
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [req]
    mock_db.execute.return_value = mock_result

    async def iter_recently_added_items(_: str):
        yield {
            "type": "movie",
            "rating_key": "movie-111",
            "title": "Movie A",
            "added_at": int(datetime(2026, 4, 19, 12, 1, tzinfo=UTC).timestamp()),
            "guids": ("tmdb://111",),
            "Media": [],
        }

    mock_plex.iter_recently_added_items = iter_recently_added_items
    mock_plex.lookup_movie_by_tmdb = AsyncMock(side_effect=Exception("boom"))

    result = await service.incremental_recent_scan()

    assert result.completed_requests == 0
    assert result.metrics.scanned_items == 1
    assert result.metrics.matched_requests == 0
    assert result.metrics.skipped_on_error_items == 1
    assert result.clean_run is False
    assert result.last_error == (
        "Incremental recent Plex scan had transient request probe errors; checkpoint retained"
    )


@pytest.mark.asyncio
async def test_incremental_recent_scan_reports_non_authoritative_movie_fallback(
    service, mock_db, mock_plex
):
    req = make_request(id=1, tmdb_id=111, title="Movie A")
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [req]
    mock_db.execute.return_value = mock_result

    async def iter_recently_added_items(_: str):
        yield {
            "type": "movie",
            "rating_key": "movie-111",
            "title": "Movie A",
            "added_at": int(datetime(2026, 4, 19, 12, 1, tzinfo=UTC).timestamp()),
            "guids": ("tmdb://111",),
            "Media": [],
        }

    mock_plex.iter_recently_added_items = iter_recently_added_items
    mock_plex.lookup_movie_by_tmdb = AsyncMock(
        return_value=PlexLookupResult(item=None, authoritative=False)
    )

    with patch.object(service.lifecycle, "transition", new_callable=AsyncMock) as mock_transition:
        result = await service.incremental_recent_scan()

    assert result.completed_requests == 0
    assert result.metrics.scanned_items == 1
    assert result.metrics.matched_requests == 0
    assert result.metrics.skipped_on_error_items == 1
    assert result.clean_run is False
    mock_plex.lookup_movie_by_tmdb.assert_awaited_once_with(111)
    mock_plex.check_movie_available.assert_not_called()
    mock_transition.assert_not_awaited()


@pytest.mark.asyncio
async def test_incremental_recent_scan_reports_non_authoritative_tv_fallback(
    service, mock_db, mock_plex
):
    req = make_request(
        id=2,
        media_type=MediaType.TV,
        tmdb_id=222,
        title="Show B",
        seasons=[make_season(1, [make_episode(1)])],
    )
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [req]
    mock_db.execute.return_value = mock_result

    async def iter_recently_added_items(_: str):
        yield {
            "type": "show",
            "title": "Show B",
            "added_at": int(datetime(2026, 4, 19, 12, 1, tzinfo=UTC).timestamp()),
            "guids": ("tmdb://222",),
            "Media": [],
        }

    mock_plex.iter_recently_added_items = iter_recently_added_items
    mock_plex.lookup_show_by_tmdb = AsyncMock(
        return_value=PlexLookupResult(
            item={"rating_key": "show-222", "Media": []},
            authoritative=True,
        )
    )
    mock_plex.get_episode_availability_result = AsyncMock(
        return_value=PlexEpisodeAvailabilityResult(availability={}, authoritative=False)
    )

    with patch.object(service.lifecycle, "transition", new_callable=AsyncMock) as mock_transition:
        result = await service.incremental_recent_scan()

    assert result.completed_requests == 0
    assert result.metrics.scanned_items == 1
    assert result.metrics.matched_requests == 0
    assert result.metrics.skipped_on_error_items == 1
    assert result.clean_run is False
    mock_plex.lookup_show_by_tmdb.assert_awaited_once_with(222)
    mock_plex.get_show_by_tmdb.assert_not_called()
    mock_plex.get_show_by_tvdb.assert_not_called()
    mock_plex.get_episode_availability_result.assert_awaited_once_with("show-222")
    mock_plex.get_episode_availability.assert_not_called()
    mock_transition.assert_not_awaited()
