import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
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
    service.scan_state.recover_stale_lock = AsyncMock()
    service.scan_state.acquire_lock = AsyncMock(return_value=SimpleNamespace(checkpoint_at=None))
    service.scan_state.release_lock = AsyncMock()

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
                "rating_key": "show",
                "title": "Show 1",
                "added_at": int(datetime(2026, 4, 19, 12, 0, tzinfo=UTC).timestamp()),
                "guids": ("tmdb://901",),
                "Media": [{"id": 1}],
            }
            yield {
                "type": "show",
                "rating_key": "show",
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
    first_transition_started = asyncio.Event()
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
        assert first_transition_started.is_set() is False

        release_writes.set()
        result = await scan_task

    assert result.completed_requests == 2
    assert result.metrics.scanned_items == 2
    assert result.metrics.matched_requests == 2
    assert result.metrics.deduped_items == 0
    assert result.metrics.skipped_on_error_items == 0
    assert max_active_writes == 1
    assert first_transition_started.is_set() is False
    assert mock_reconcile.await_count == 2
    assert mock_transition.await_count == 0


@pytest.mark.asyncio
async def test_incremental_recent_scan_collapses_duplicate_media_ids_within_cycle(
    service, mock_db, mock_plex
):
    service.scan_state.recover_stale_lock = AsyncMock()
    service.scan_state.acquire_lock = AsyncMock(return_value=SimpleNamespace(checkpoint_at=None))
    service.scan_state.release_lock = AsyncMock()

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
async def test_incremental_recent_scan_uses_checkpoint_buffer_and_recent_matches_only(
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

    previous_checkpoint = datetime(2026, 4, 19, 12, 0, tzinfo=UTC)
    run_started = datetime(2026, 4, 19, 12, 30, tzinfo=UTC)

    state = SimpleNamespace(checkpoint_at=previous_checkpoint)
    service.scan_state.recover_stale_lock = AsyncMock()
    service.scan_state.acquire_lock = AsyncMock(return_value=state)
    service.scan_state.release_lock = AsyncMock()

    async def iter_recently_added_items(media_type: str):
        if media_type == "movie":
            yield {
                "type": "movie",
                "rating_key": "movie-111",
                "title": "Movie A",
                "added_at": int((previous_checkpoint - timedelta(minutes=5)).timestamp()),
                "guids": ("tmdb://111",),
                "Media": [{"id": 1}],
            }
            yield {
                "type": "movie",
                "rating_key": "movie-old",
                "title": "Too Old",
                "added_at": int((previous_checkpoint - timedelta(minutes=11)).timestamp()),
                "guids": ("tmdb://999",),
                "Media": [{"id": 1}],
            }
            return
        yield {
            "type": "show",
            "rating_key": "show-222",
            "title": "Show B",
            "added_at": int((previous_checkpoint + timedelta(minutes=1)).timestamp()),
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

    async def reconcile_to_available(request, seasons, availability):
        return await set_request_status(request, RequestStatus.AVAILABLE, seasons, availability)

    with (
        patch.object(service, "_current_time", side_effect=[run_started, run_started, run_started]),
        patch.object(
            service.episode_sync,
            "reconcile_existing_seasons_from_plex",
            new_callable=AsyncMock,
        ) as mock_reconcile,
        patch.object(service.lifecycle, "transition", new_callable=AsyncMock) as mock_transition,
    ):
        mock_reconcile.side_effect = reconcile_to_available
        result = await service.incremental_recent_scan()

    assert result.completed_requests == 2
    assert result.metrics.scanned_items == 2
    assert result.metrics.matched_requests == 2
    assert result.metrics.skipped_on_error_items == 0
    assert result.metrics.checkpoint.previous_checkpoint_at == previous_checkpoint
    assert result.metrics.checkpoint.current_checkpoint_at == run_started
    assert result.metrics.checkpoint.advanced is True
    mock_plex.check_movie_available.assert_not_called()
    mock_plex.get_episode_availability_result.assert_awaited_once_with("show-222")
    mock_transition.assert_awaited_once_with(1, RequestStatus.COMPLETED, reason="Found on Plex")
    mock_reconcile.assert_awaited_once_with(req2, req2.seasons, {(1, 1): True})
    service.scan_state.release_lock.assert_awaited_once()
    release_call = service.scan_state.release_lock.await_args
    assert release_call is not None
    release_kwargs = release_call.kwargs
    assert release_kwargs["success"] is True
    assert release_kwargs["checkpoint_at"] == run_started
    assert release_kwargs["last_error"] is None
    assert release_kwargs["metrics_payload"]["checkpoint"]["advanced"] is True


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

    service.scan_state.recover_stale_lock = AsyncMock()
    service.scan_state.acquire_lock = AsyncMock(return_value=SimpleNamespace(checkpoint_at=None))
    service.scan_state.release_lock = AsyncMock()

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
async def test_incremental_recent_scan_skips_when_lock_is_held(service, mock_db, mock_plex):
    service.scan_state.recover_stale_lock = AsyncMock()
    service.scan_state.acquire_lock = AsyncMock(return_value=None)
    service.scan_state.release_lock = AsyncMock()

    result = await service.incremental_recent_scan()

    assert result.completed_requests == 0
    assert result.metrics.scanned_items == 0
    assert result.metrics.matched_requests == 0
    assert result.metrics.deduped_items == 0
    assert result.metrics.skipped_on_error_items == 0
    mock_db.execute.assert_not_called()
    service.scan_state.release_lock.assert_not_awaited()


@pytest.mark.asyncio
async def test_incremental_recent_scan_retains_checkpoint_on_transient_recent_scan_failure(
    service, mock_db, mock_plex
):
    req = make_request(id=1, tmdb_id=111, title="Movie A")
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [req]
    mock_db.execute.return_value = mock_result

    previous_checkpoint = datetime(2026, 4, 19, 12, 0, tzinfo=UTC)
    service.scan_state.recover_stale_lock = AsyncMock()
    service.scan_state.acquire_lock = AsyncMock(
        return_value=SimpleNamespace(checkpoint_at=previous_checkpoint)
    )
    service.scan_state.release_lock = AsyncMock()

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
    assert result.metrics.checkpoint.previous_checkpoint_at == previous_checkpoint
    assert result.metrics.checkpoint.current_checkpoint_at == previous_checkpoint
    assert result.metrics.checkpoint.advanced is False
    assert result.clean_run is False
    assert result.last_error == "recently added unavailable"
    service.scan_state.release_lock.assert_awaited_once()
    release_call = service.scan_state.release_lock.await_args
    assert release_call is not None
    release_kwargs = release_call.kwargs
    assert release_kwargs["success"] is False
    assert release_kwargs["checkpoint_at"] == previous_checkpoint
    assert release_kwargs["last_error"] == "recently added unavailable"
    assert release_kwargs["metrics_payload"]["checkpoint"]["advanced"] is False


@pytest.mark.asyncio
async def test_incremental_recent_scan_retains_checkpoint_on_request_probe_error(
    service, mock_db, mock_plex
):
    req = make_request(id=1, tmdb_id=111, title="Movie A")
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [req]
    mock_db.execute.return_value = mock_result

    previous_checkpoint = datetime(2026, 4, 19, 12, 0, tzinfo=UTC)
    service.scan_state.recover_stale_lock = AsyncMock()
    service.scan_state.acquire_lock = AsyncMock(
        return_value=SimpleNamespace(checkpoint_at=previous_checkpoint)
    )
    service.scan_state.release_lock = AsyncMock()

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
    assert result.metrics.checkpoint.previous_checkpoint_at == previous_checkpoint
    assert result.metrics.checkpoint.current_checkpoint_at == previous_checkpoint
    assert result.metrics.checkpoint.advanced is False
    assert result.clean_run is False
    assert result.last_error == (
        "Incremental recent Plex scan had transient request probe errors; checkpoint retained"
    )
    release_call = service.scan_state.release_lock.await_args
    assert release_call is not None
    release_kwargs = release_call.kwargs
    assert release_kwargs["success"] is False
    assert release_kwargs["checkpoint_at"] == previous_checkpoint
    assert release_kwargs["last_error"] == (
        "Incremental recent Plex scan had transient request probe errors; checkpoint retained"
    )
    assert release_kwargs["metrics_payload"]["skipped_on_error_items"] == 1


@pytest.mark.asyncio
async def test_incremental_recent_scan_retains_checkpoint_on_non_authoritative_movie_fallback(
    service, mock_db, mock_plex
):
    req = make_request(id=1, tmdb_id=111, title="Movie A")
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [req]
    mock_db.execute.return_value = mock_result

    previous_checkpoint = datetime(2026, 4, 19, 12, 0, tzinfo=UTC)
    service.scan_state.recover_stale_lock = AsyncMock()
    service.scan_state.acquire_lock = AsyncMock(
        return_value=SimpleNamespace(checkpoint_at=previous_checkpoint)
    )
    service.scan_state.release_lock = AsyncMock()

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
    assert result.metrics.checkpoint.previous_checkpoint_at == previous_checkpoint
    assert result.metrics.checkpoint.current_checkpoint_at == previous_checkpoint
    assert result.metrics.checkpoint.advanced is False
    mock_plex.lookup_movie_by_tmdb.assert_awaited_once_with(111)
    mock_plex.check_movie_available.assert_not_called()
    mock_transition.assert_not_awaited()
    release_call = service.scan_state.release_lock.await_args
    assert release_call is not None
    release_kwargs = release_call.kwargs
    assert release_kwargs["success"] is False
    assert release_kwargs["checkpoint_at"] == previous_checkpoint
    assert release_kwargs["metrics_payload"]["skipped_on_error_items"] == 1


@pytest.mark.asyncio
async def test_incremental_recent_scan_retains_checkpoint_on_non_authoritative_tv_fallback(
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

    previous_checkpoint = datetime(2026, 4, 19, 12, 0, tzinfo=UTC)
    service.scan_state.recover_stale_lock = AsyncMock()
    service.scan_state.acquire_lock = AsyncMock(
        return_value=SimpleNamespace(checkpoint_at=previous_checkpoint)
    )
    service.scan_state.release_lock = AsyncMock()

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
    assert result.metrics.checkpoint.previous_checkpoint_at == previous_checkpoint
    assert result.metrics.checkpoint.current_checkpoint_at == previous_checkpoint
    assert result.metrics.checkpoint.advanced is False
    mock_plex.lookup_show_by_tmdb.assert_awaited_once_with(222)
    mock_plex.get_show_by_tmdb.assert_not_called()
    mock_plex.get_show_by_tvdb.assert_not_called()
    mock_plex.get_episode_availability_result.assert_awaited_once_with("show-222")
    mock_plex.get_episode_availability.assert_not_called()
    mock_transition.assert_not_awaited()
    release_call = service.scan_state.release_lock.await_args
    assert release_call is not None
    release_kwargs = release_call.kwargs
    assert release_kwargs["success"] is False
    assert release_kwargs["checkpoint_at"] == previous_checkpoint
    assert release_kwargs["metrics_payload"]["skipped_on_error_items"] == 1
