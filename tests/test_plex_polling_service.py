"""Tests for PlexPollingService."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.siftarr.models.request import MediaType, Request, RequestStatus
from app.siftarr.services.plex_polling_service import PlexPollingService


def _make_request(
    id: int = 1,
    media_type: MediaType = MediaType.MOVIE,
    status: RequestStatus = RequestStatus.SEARCHING,
    tmdb_id: int | None = 12345,
    tvdb_id: int | None = None,
    title: str = "Test",
    seasons: list | None = None,
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
        plex.settings = SimpleNamespace(plex_sync_concurrency=16)
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
        """TV request where all episodes are on Plex should be completed."""
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

        with patch.object(
            service.lifecycle, "transition", new_callable=AsyncMock
        ) as mock_transition:
            mock_transition.return_value = req
            completed = await service.poll()

        assert completed == 1
        mock_transition.assert_called_once_with(
            req.id, RequestStatus.COMPLETED, reason="All episodes found on Plex"
        )

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
        """Previously partial TV requests should still be polled and allowed to complete."""
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

        with patch.object(
            service.lifecycle, "transition", new_callable=AsyncMock
        ) as mock_transition:
            mock_transition.return_value = req
            completed = await service.poll()

        assert completed == 1
        mock_transition.assert_called_once_with(
            req.id, RequestStatus.COMPLETED, reason="All episodes found on Plex"
        )

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

        with patch.object(
            service.lifecycle, "transition", new_callable=AsyncMock
        ) as mock_transition:
            mock_transition.return_value = req
            completed = await service.poll()

        assert completed == 1
        mock_plex.get_show_by_tvdb.assert_called_once_with(888)

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
    async def test_poll_applies_completion_updates_after_probe_phase(
        self, service, mock_db, mock_plex
    ):
        """DB mutations should begin only after probes finish and remain serialized."""
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
        mock_plex.get_show_by_tmdb.return_value = {"rating_key": "show"}

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

        async def get_episode_availability(_: str) -> dict[tuple[int, int], bool]:
            nonlocal probes_started, probes_finished
            async with state_lock:
                probes_started += 1
                if probes_started == 2:
                    probes_ready.set()
            await release_probes.wait()
            async with state_lock:
                probes_finished += 1
            return {(1, 1): True, (1, 2): True}

        update_calls = 0

        async def update_episode_statuses(req, completed_episodes) -> None:
            nonlocal update_calls
            nonlocal active_writes, max_active_writes
            assert probes_finished == 2
            assert completed_episodes == frozenset({(1, 1), (1, 2)})
            update_calls += 1
            if update_calls == 1:
                first_write_started.set()
            else:
                second_write_started.set()
            active_writes += 1
            max_active_writes = max(max_active_writes, active_writes)
            await release_writes.wait()
            active_writes -= 1

        async def transition(*args, **kwargs):
            nonlocal active_writes, max_active_writes
            assert probes_finished == 2
            first_transition_started.set()
            active_writes += 1
            max_active_writes = max(max_active_writes, active_writes)
            await release_writes.wait()
            active_writes -= 1
            return args[0]

        mock_plex.get_episode_availability.side_effect = get_episode_availability

        with (
            patch.object(
                service, "_update_episode_statuses", side_effect=update_episode_statuses
            ) as mock_update,
            patch.object(
                service.lifecycle, "transition", side_effect=transition
            ) as mock_transition,
        ):
            poll_task = asyncio.create_task(service.poll())
            await asyncio.wait_for(probes_ready.wait(), timeout=1)

            assert probes_finished == 0
            mock_update.assert_not_called()
            mock_transition.assert_not_called()

            release_probes.set()
            await asyncio.wait_for(first_write_started.wait(), timeout=1)

            assert second_write_started.is_set() is False
            assert first_transition_started.is_set() is False

            release_writes.set()
            completed = await poll_task

        assert completed == 2
        assert max_active_writes == 1
        assert mock_update.await_count == 2
        assert mock_transition.await_count == 2

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
