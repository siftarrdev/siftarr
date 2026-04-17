"""Tests for PlexPollingService."""

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
        return AsyncMock()

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
    async def test_update_episode_statuses(self, service, mock_db):
        """Episode and season statuses should be updated."""
        ep1 = _make_episode(1, RequestStatus.SEARCHING)
        ep2 = _make_episode(2, RequestStatus.SEARCHING)
        season = _make_season(1, [ep1, ep2])
        req = _make_request(media_type=MediaType.TV, seasons=[season])

        availability = {(1, 1): True, (1, 2): True}
        await service._update_episode_statuses(req, availability)

        assert ep1.status == RequestStatus.COMPLETED
        assert ep2.status == RequestStatus.COMPLETED
        assert season.status == RequestStatus.COMPLETED
        mock_db.commit.assert_called_once()
