"""Tests for PlexPollingService."""

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.siftarr.models.request import MediaType, Request, RequestStatus
from app.siftarr.services.plex_polling_service import PlexPollingService
from app.siftarr.services.plex_service import (
    PlexEpisodeAvailabilityResult,
    PlexLookupResult,
    PlexTransientScanError,
)


def _make_request(
    id: int = 1,
    media_type: MediaType = MediaType.MOVIE,
    status: RequestStatus = RequestStatus.SEARCHING,
    tmdb_id: int | None = 12345,
    tvdb_id: int | None = None,
    title: str = "Test",
    seasons: list | None = None,
    plex_rating_key: str | None = None,
) -> MagicMock:
    req = MagicMock(spec=Request)
    req.id = id
    req.media_type = media_type
    req.status = status
    req.tmdb_id = tmdb_id
    req.tvdb_id = tvdb_id
    req.title = title
    req.seasons = seasons or []
    req.requested_episodes = None
    req.plex_rating_key = plex_rating_key
    return req


def _make_season(season_number: int, episodes: list) -> MagicMock:
    season = MagicMock()
    season.season_number = season_number
    season.episodes = episodes
    season.status = RequestStatus.SEARCHING
    return season


def _make_episode(
    episode_number: int, status: RequestStatus = RequestStatus.SEARCHING
) -> MagicMock:
    ep = MagicMock()
    ep.episode_number = episode_number
    ep.status = status
    ep.air_date = None
    return ep


class TestPlexPollingService:
    """Test cases for PlexPollingService."""

    @pytest.fixture
    def mock_db(self):
        db = AsyncMock()
        return db

    @pytest.fixture
    def mock_plex(self):
        plex = AsyncMock()
        plex.settings = SimpleNamespace(
            plex_sync_concurrency=16,
            plex_checkpoint_buffer_minutes=10,
            plex_recent_scan_interval_minutes=5,
        )

        @asynccontextmanager
        async def scan_cycle():
            yield plex

        plex.scan_cycle = scan_cycle
        return plex

    @pytest.fixture
    def service(self, mock_db, mock_plex):
        return PlexPollingService(mock_db, mock_plex)

    @pytest.mark.asyncio
    async def test_poll_no_active_requests(self, service, mock_db):
        """No active requests means nothing to do."""
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_db.execute.return_value = mock_result

        completed = await service.poll()
        assert completed == 0

    @pytest.mark.asyncio
    async def test_poll_movie_available(self, service, mock_db, mock_plex):
        """Movie found on Plex should be completed."""
        req = _make_request()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [req]
        mock_db.execute.return_value = mock_result

        mock_plex.check_movie_available.return_value = True

        with patch.object(
            service.lifecycle, "transition", new_callable=AsyncMock
        ) as mock_transition:
            mock_transition.return_value = req
            completed = await service.poll()

        assert completed == 1
        mock_plex.check_movie_available.assert_called_once_with(12345)
        mock_transition.assert_called_once_with(1, RequestStatus.COMPLETED, reason="Found on Plex")

    @pytest.mark.asyncio
    async def test_poll_movie_not_available(self, service, mock_db, mock_plex):
        """Movie not on Plex should not be completed."""
        req = _make_request()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [req]
        mock_db.execute.return_value = mock_result

        mock_plex.check_movie_available.return_value = False

        with patch.object(
            service.lifecycle, "transition", new_callable=AsyncMock
        ) as mock_transition:
            completed = await service.poll()

        assert completed == 0
        mock_transition.assert_not_called()

    @pytest.mark.asyncio
    async def test_poll_movie_no_tmdb_id(self, service, mock_db, mock_plex):
        """Movie without tmdb_id is skipped."""
        req = _make_request(tmdb_id=None)
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [req]
        mock_db.execute.return_value = mock_result

        completed = await service.poll()
        assert completed == 0
        mock_plex.check_movie_available.assert_not_called()

    @pytest.mark.asyncio
    async def test_poll_tv_all_episodes_available(self, service, mock_db, mock_plex):
        """TV request where all episodes are on Plex should be reconciled via aggregation."""
        ep1 = _make_episode(1)
        ep2 = _make_episode(2)
        season = _make_season(1, [ep1, ep2])
        req = _make_request(
            media_type=MediaType.TV,
            tmdb_id=999,
            seasons=[season],
        )
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [req]
        mock_db.execute.return_value = mock_result

        mock_plex.get_show_by_tmdb.return_value = {"rating_key": "42"}
        mock_plex.get_episode_availability.return_value = {
            (1, 1): True,
            (1, 2): True,
        }

        async def reconcile_to_available(request, seasons, availability):
            return await _set_request_status(
                request, RequestStatus.AVAILABLE, seasons, availability
            )

        with patch.object(
            service.episode_sync,
            "reconcile_existing_seasons_from_plex",
            new_callable=AsyncMock,
        ) as mock_reconcile, patch.object(
            service.lifecycle, "transition", new_callable=AsyncMock
        ) as mock_transition:
            mock_reconcile.side_effect = reconcile_to_available
            completed = await service.poll()

        assert completed == 1
        mock_reconcile.assert_awaited_once_with(
            req,
            req.seasons,
            {(1, 1): True, (1, 2): True},
        )
        mock_transition.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_poll_tv_partial_episodes(self, service, mock_db, mock_plex):
        """TV request where only some episodes are available should not complete."""
        ep1 = _make_episode(1)
        ep2 = _make_episode(2)
        season = _make_season(1, [ep1, ep2])
        req = _make_request(
            media_type=MediaType.TV,
            tmdb_id=999,
            seasons=[season],
        )
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [req]
        mock_db.execute.return_value = mock_result

        mock_plex.get_show_by_tmdb.return_value = {"rating_key": "42"}
        mock_plex.get_episode_availability.return_value = {
            (1, 1): True,
            (1, 2): False,
        }

        with patch.object(
            service.lifecycle, "transition", new_callable=AsyncMock
        ) as mock_transition:
            completed = await service.poll()

        assert completed == 0
        mock_transition.assert_not_called()

    @pytest.mark.asyncio
    async def test_poll_partially_available_request_can_complete(self, service, mock_db, mock_plex):
        """Previously partial TV requests should still be polled and recomputed."""
        ep1 = _make_episode(1, status=RequestStatus.COMPLETED)
        ep2 = _make_episode(2, status=RequestStatus.PENDING)
        season = _make_season(1, [ep1, ep2])
        req = _make_request(
            media_type=MediaType.TV,
            status=RequestStatus.PARTIALLY_AVAILABLE,
            tmdb_id=999,
            seasons=[season],
        )
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [req]
        mock_db.execute.return_value = mock_result

        mock_plex.get_show_by_tmdb.return_value = {"rating_key": "42"}
        mock_plex.get_episode_availability.return_value = {
            (1, 1): True,
            (1, 2): True,
        }

        async def reconcile_to_available(request, seasons, availability):
            return await _set_request_status(
                request, RequestStatus.AVAILABLE, seasons, availability
            )

        with patch.object(
            service.episode_sync,
            "reconcile_existing_seasons_from_plex",
            new_callable=AsyncMock,
        ) as mock_reconcile, patch.object(
            service.lifecycle, "transition", new_callable=AsyncMock
        ) as mock_transition:
            mock_reconcile.side_effect = reconcile_to_available
            completed = await service.poll()

        assert completed == 1
        mock_reconcile.assert_awaited_once_with(
            req,
            req.seasons,
            {(1, 1): True, (1, 2): True},
        )
        mock_transition.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_poll_tv_show_not_in_plex(self, service, mock_db, mock_plex):
        """TV show not in Plex should not complete."""
        ep1 = _make_episode(1)
        season = _make_season(1, [ep1])
        req = _make_request(
            media_type=MediaType.TV,
            tmdb_id=999,
            tvdb_id=None,
            seasons=[season],
        )
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [req]
        mock_db.execute.return_value = mock_result

        mock_plex.get_show_by_tmdb.return_value = None

        completed = await service.poll()
        assert completed == 0

    @pytest.mark.asyncio
    async def test_poll_tv_fallback_to_tvdb(self, service, mock_db, mock_plex):
        """TV show should fall back to TVDB lookup if TMDB fails."""
        ep1 = _make_episode(1)
        season = _make_season(1, [ep1])
        req = _make_request(
            media_type=MediaType.TV,
            tmdb_id=999,
            tvdb_id=888,
            seasons=[season],
        )
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [req]
        mock_db.execute.return_value = mock_result

        mock_plex.get_show_by_tmdb.return_value = None
        mock_plex.get_show_by_tvdb.return_value = {"rating_key": "55"}
        mock_plex.get_episode_availability.return_value = {(1, 1): True}

        async def reconcile_to_available(request, seasons, availability):
            return await _set_request_status(
                request, RequestStatus.AVAILABLE, seasons, availability
            )

        with patch.object(
            service.episode_sync,
            "reconcile_existing_seasons_from_plex",
            new_callable=AsyncMock,
        ) as mock_reconcile, patch.object(
            service.lifecycle, "transition", new_callable=AsyncMock
        ) as mock_transition:
            mock_reconcile.side_effect = reconcile_to_available
            completed = await service.poll()

        assert completed == 1
        mock_plex.get_show_by_tvdb.assert_called_once_with(888)
        mock_reconcile.assert_awaited_once_with(req, req.seasons, {(1, 1): True})
        mock_transition.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_poll_error_handling(self, service, mock_db, mock_plex):
        """Errors for individual requests should not stop polling others."""
        req1 = _make_request(id=1, tmdb_id=111)
        req2 = _make_request(id=2, tmdb_id=222)
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [req1, req2]
        mock_db.execute.return_value = mock_result

        mock_plex.check_movie_available.side_effect = [Exception("boom"), True]

        with patch.object(
            service.lifecycle, "transition", new_callable=AsyncMock
        ) as mock_transition:
            mock_transition.return_value = req2
            completed = await service.poll()

        assert completed == 1

    @pytest.mark.asyncio
    async def test_poll_caps_probe_concurrency(self, service, mock_db, mock_plex):
        """Probe concurrency should respect settings.plex_sync_concurrency."""
        requests = [_make_request(id=index, tmdb_id=100 + index) for index in range(1, 5)]
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

        with patch.object(
            service.lifecycle, "transition", new_callable=AsyncMock
        ) as mock_transition:
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
    async def test_incremental_recent_scan_applies_completion_updates_after_probe_phase(
        self, service, mock_db, mock_plex
    ):
        """DB mutations should begin only after probes finish and remain serialized."""
        service.scan_state.recover_stale_lock = AsyncMock()
        service.scan_state.acquire_lock = AsyncMock(
            return_value=SimpleNamespace(checkpoint_at=None)
        )
        service.scan_state.release_lock = AsyncMock()

        req1 = _make_request(
            id=1,
            media_type=MediaType.TV,
            tmdb_id=901,
            title="Show 1",
            seasons=[_make_season(1, [_make_episode(1), _make_episode(2)])],
        )
        req2 = _make_request(
            id=2,
            media_type=MediaType.TV,
            tmdb_id=902,
            title="Show 2",
            seasons=[_make_season(1, [_make_episode(1), _make_episode(2)])],
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
            nonlocal reconcile_calls
            nonlocal active_writes, max_active_writes
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
            patch.object(
                service.lifecycle, "transition", new_callable=AsyncMock
            ) as mock_transition,
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
        self, service, mock_db, mock_plex
    ):
        """Duplicate requests for the same media should share one probe per cycle."""
        service.scan_state.recover_stale_lock = AsyncMock()
        service.scan_state.acquire_lock = AsyncMock(
            return_value=SimpleNamespace(checkpoint_at=None)
        )
        service.scan_state.release_lock = AsyncMock()

        req1 = _make_request(id=1, tmdb_id=111, title="Movie A")
        req2 = _make_request(id=2, tmdb_id=111, title="Movie A Duplicate")
        req3 = _make_request(id=3, tmdb_id=222, title="Movie B")
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

        with patch.object(
            service.lifecycle, "transition", new_callable=AsyncMock
        ) as mock_transition:
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
        self, service, mock_db, mock_plex
    ):
        """Recently-added items should be windowed by checkpoint and only affect matching requests."""
        req1 = _make_request(id=1, tmdb_id=111, title="Movie A")
        req2 = _make_request(
            id=2,
            media_type=MediaType.TV,
            tmdb_id=222,
            title="Show B",
            seasons=[_make_season(1, [_make_episode(1)])],
        )
        req3 = _make_request(id=3, tmdb_id=999, title="Movie C")
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
            return await _set_request_status(
                request, RequestStatus.AVAILABLE, seasons, availability
            )

        with (
            patch.object(
                service, "_current_time", side_effect=[run_started, run_started, run_started]
            ),
            patch.object(
                service.episode_sync,
                "reconcile_existing_seasons_from_plex",
                new_callable=AsyncMock,
            ) as mock_reconcile,
            patch.object(
                service.lifecycle, "transition", new_callable=AsyncMock
            ) as mock_transition,
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
        self, service, mock_db, mock_plex
    ):
        """Recent items with enough data should avoid targeted request lookups."""
        movie_req = _make_request(id=1, tmdb_id=111, title="Movie A")
        tv_req = _make_request(
            id=2,
            media_type=MediaType.TV,
            tmdb_id=222,
            title="Show B",
            seasons=[_make_season(1, [_make_episode(1)])],
        )
        movie_fallback_req = _make_request(id=3, tmdb_id=333, title="Movie C")
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [movie_req, tv_req, movie_fallback_req]
        mock_db.execute.return_value = mock_result

        service.scan_state.recover_stale_lock = AsyncMock()
        service.scan_state.acquire_lock = AsyncMock(
            return_value=SimpleNamespace(checkpoint_at=None)
        )
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
            return await _set_request_status(
                request, RequestStatus.AVAILABLE, seasons, availability
            )

        with patch.object(
            service.episode_sync,
            "reconcile_existing_seasons_from_plex",
            new_callable=AsyncMock,
        ) as mock_reconcile, patch.object(
            service.lifecycle, "transition", new_callable=AsyncMock
        ) as mock_transition:
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
    async def test_incremental_recent_scan_skips_when_lock_is_held(
        self, service, mock_db, mock_plex
    ):
        """Incremental scans should exit cleanly when another worker owns the lock."""
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
        self, service, mock_db, mock_plex
    ):
        """Transient Plex scan failures should keep the prior checkpoint and record metrics."""
        req = _make_request(id=1, tmdb_id=111, title="Movie A")
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
        self, service, mock_db, mock_plex
    ):
        """Targeted request probe errors should not advance the checkpoint."""
        req = _make_request(id=1, tmdb_id=111, title="Movie A")
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
        self, service, mock_db, mock_plex
    ):
        """Incremental targeted fallback must preserve inconclusive movie lookups."""
        req = _make_request(id=1, tmdb_id=111, title="Movie A")
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

        with patch.object(
            service.lifecycle, "transition", new_callable=AsyncMock
        ) as mock_transition:
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
        self, service, mock_db, mock_plex
    ):
        """Incremental targeted TV fallback must preserve inconclusive lookup/episode probes."""
        req = _make_request(
            id=2,
            media_type=MediaType.TV,
            tmdb_id=222,
            title="Show B",
            seasons=[_make_season(1, [_make_episode(1)])],
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
                item={"rating_key": "show-222", "Media": []}, authoritative=True
            )
        )
        mock_plex.get_episode_availability_result = AsyncMock(
            return_value=PlexEpisodeAvailabilityResult(availability={}, authoritative=False)
        )

        with patch.object(
            service.lifecycle, "transition", new_callable=AsyncMock
        ) as mock_transition:
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

    @pytest.mark.asyncio
    async def test_full_reconcile_scan_builds_authoritative_presence_and_completes_matches(
        self, service, mock_db, mock_plex
    ):
        """Full reconcile should use full-library presence to complete matching requests."""
        movie_req = _make_request(id=1, tmdb_id=111, title="Movie A")
        tv_req = _make_request(
            id=2,
            media_type=MediaType.TV,
            status=RequestStatus.PENDING,
            tmdb_id=222,
            title="Show B",
            seasons=[_make_season(1, [_make_episode(1), _make_episode(2)])],
        )
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [movie_req, tv_req]
        mock_db.execute.return_value = mock_result

        mock_plex.scan_library_items = AsyncMock(
            side_effect=[
                SimpleNamespace(
                    items=(
                        {
                            "type": "movie",
                            "rating_key": "movie-111",
                            "guids": ("tmdb://111",),
                            "Media": [{"id": 1}],
                        },
                    ),
                    authoritative=True,
                    failed_sections=(),
                ),
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
            return_value=SimpleNamespace(
                authoritative=True, availability={(1, 1): True, (1, 2): True}
            )
        )

        async def reconcile_existing_seasons_from_plex(req, seasons, availability):
            assert req is tv_req
            assert seasons == tv_req.seasons
            assert availability == {(1, 1): True, (1, 2): True}
            req.status = RequestStatus.AVAILABLE
            return seasons

        service.episode_sync.reconcile_existing_seasons_from_plex = AsyncMock(
            side_effect=reconcile_existing_seasons_from_plex
        )

        with patch.object(
            service.lifecycle, "transition", new_callable=AsyncMock
        ) as mock_transition:
            result = await service.full_reconcile_scan()

        assert result.completed_requests == 2
        assert result.metrics.scanned_items == 2
        assert result.metrics.matched_requests == 2
        assert result.metrics.deduped_items == 0
        assert result.metrics.downgraded_requests == 0
        assert result.metrics.skipped_on_error_items == 0
        service.episode_sync.reconcile_existing_seasons_from_plex.assert_awaited_once()
        mock_transition.assert_awaited_once_with(1, RequestStatus.COMPLETED, reason="Found on Plex")


async def _set_request_status(request, status, seasons, availability):
    request.status = status
    return seasons

    @pytest.mark.asyncio
    async def test_full_reconcile_scan_downgrades_stale_movie_on_clean_authoritative_miss(
        self, service, mock_db, mock_plex
    ):
        """Authoritative full scans may negatively reconcile stale movie completion."""
        req = _make_request(id=1, tmdb_id=111, title="Movie A", status=RequestStatus.COMPLETED)
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

        with patch.object(
            service.lifecycle, "transition", new_callable=AsyncMock
        ) as mock_transition:
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
        self, service, mock_db, mock_plex
    ):
        """Transient full-scan failures must not create false negative movie downgrades."""
        req = _make_request(id=1, tmdb_id=111, title="Movie A", status=RequestStatus.COMPLETED)
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

        with patch.object(
            service.lifecycle, "transition", new_callable=AsyncMock
        ) as mock_transition:
            result = await service.full_reconcile_scan()

        assert result.completed_requests == 0
        assert result.metrics.downgraded_requests == 0
        assert result.metrics.skipped_on_error_items == 1
        mock_transition.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_full_reconcile_scan_recomputes_tv_status_via_episode_sync_on_authoritative_miss(
        self, service, mock_db, mock_plex
    ):
        """TV downgrades should flow through episode-sync recomputation, not ad-hoc mutation."""
        req = _make_request(
            id=2,
            media_type=MediaType.TV,
            status=RequestStatus.COMPLETED,
            tmdb_id=222,
            title="Show B",
            seasons=[_make_season(1, [_make_episode(1), _make_episode(2)])],
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

        with patch.object(
            service.lifecycle, "transition", new_callable=AsyncMock
        ) as mock_transition:
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
        self, service, mock_db, mock_plex
    ):
        """TV requests should stay untouched when show-level episode availability is inconclusive."""
        req = _make_request(
            id=2,
            media_type=MediaType.TV,
            status=RequestStatus.PARTIALLY_AVAILABLE,
            tmdb_id=222,
            title="Show B",
            seasons=[_make_season(1, [_make_episode(1)])],
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

        with patch.object(
            service.lifecycle, "transition", new_callable=AsyncMock
        ) as mock_transition:
            result = await service.full_reconcile_scan()

        assert result.completed_requests == 0
        assert result.metrics.matched_requests == 1
        assert result.metrics.downgraded_requests == 0
        assert result.metrics.skipped_on_error_items == 1
        service.episode_sync.reconcile_existing_seasons_from_plex.assert_not_called()
        mock_transition.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_full_reconcile_scan_counts_deduped_presence_items_without_double_downgrading(
        self, service, mock_db, mock_plex
    ):
        """Duplicate full-scan items should be deduped without duplicate reconciliation work."""
        req = _make_request(id=1, tmdb_id=111, title="Movie A", status=RequestStatus.AVAILABLE)
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

        with patch.object(
            service.lifecycle, "transition", new_callable=AsyncMock
        ) as mock_transition:
            result = await service.full_reconcile_scan()

        assert result.completed_requests == 0
        assert result.metrics.scanned_items == 2
        assert result.metrics.matched_requests == 1
        assert result.metrics.deduped_items == 1
        assert result.metrics.downgraded_requests == 0
        mock_transition.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_update_episode_statuses(self, service, mock_db):
        """Episode and season statuses should be updated."""
        ep1 = _make_episode(1, RequestStatus.SEARCHING)
        ep2 = _make_episode(2, RequestStatus.SEARCHING)
        season = _make_season(1, [ep1, ep2])
        req = _make_request(media_type=MediaType.TV, seasons=[season])

        completed_episodes = frozenset({(1, 1), (1, 2)})
        await service._update_episode_statuses(req, completed_episodes)

        assert ep1.status == RequestStatus.COMPLETED
        assert ep2.status == RequestStatus.COMPLETED
        assert season.status == RequestStatus.COMPLETED
        mock_db.commit.assert_called_once()
