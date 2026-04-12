"""Tests for EpisodeSyncService."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.siftarr.models.episode import Episode
from app.siftarr.models.request import MediaType, Request, RequestStatus
from app.siftarr.models.season import Season
from app.siftarr.services.episode_sync_service import EpisodeSyncService


def _make_request(**overrides):
    req = MagicMock(spec=Request)
    req.id = overrides.get("id", 1)
    req.media_type = overrides.get("media_type", MediaType.TV)
    req.tvdb_id = overrides.get("tvdb_id", 12345)
    req.tmdb_id = overrides.get("tmdb_id", 79744)
    req.overseerr_request_id = overrides.get("overseerr_request_id")
    return req


def _make_season(request_id=1, season_number=1, synced_at=None):
    season = MagicMock(spec=Season)
    season.id = season_number
    season.request_id = request_id
    season.season_number = season_number
    season.status = RequestStatus.RECEIVED
    season.synced_at = synced_at
    season.episodes = []
    return season


def _make_episode(season_id=1, episode_number=1):
    ep = MagicMock(spec=Episode)
    ep.id = episode_number
    ep.season_id = season_id
    ep.episode_number = episode_number
    ep.title = f"Episode {episode_number}"
    ep.air_date = None
    ep.status = RequestStatus.RECEIVED
    return ep


TV_DETAILS_NO_EPISODES = {
    "seasons": [
        {"seasonNumber": 0, "name": "Specials"},
        {"seasonNumber": 1, "name": "Season 1"},
    ]
}

SEASON_1_DETAILS = {
    "seasonNumber": 1,
    "episodes": [
        {"episodeNumber": 1, "title": "Pilot", "airDate": "2024-01-01"},
        {"episodeNumber": 2, "title": "Episode 2", "airDate": "2024-01-08"},
    ],
}


class TestEpisodeSyncService:
    @pytest.fixture
    def mock_db(self):
        return AsyncMock()

    @pytest.fixture
    def mock_overseerr(self):
        return AsyncMock()

    @pytest.fixture
    def service(self, mock_db, mock_overseerr):
        return EpisodeSyncService(mock_db, overseerr=mock_overseerr)

    @pytest.mark.asyncio
    async def test_sync_creates_season_and_episode_records(self, service, mock_db, mock_overseerr):
        request = _make_request(id=1, tvdb_id=12345)

        req_result = MagicMock()
        req_result.scalar_one_or_none.return_value = request

        season_result = MagicMock()
        season_result.scalar_one_or_none.return_value = None

        ep1_result = MagicMock()
        ep1_result.scalar_one_or_none.return_value = None

        ep2_result = MagicMock()
        ep2_result.scalar_one_or_none.return_value = None

        mock_db.execute.side_effect = [req_result, season_result, ep1_result, ep2_result]
        mock_db.flush = AsyncMock()
        mock_db.commit = AsyncMock()

        mock_overseerr.get_media_details.return_value = TV_DETAILS_NO_EPISODES
        mock_overseerr.get_season_details.return_value = SEASON_1_DETAILS

        seasons = await service.sync_episodes(1)

        assert len(seasons) == 1
        assert mock_db.add.call_count == 3
        mock_overseerr.get_season_details.assert_awaited_once_with(79744, 1)
        mock_db.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_sync_is_idempotent(self, service, mock_db, mock_overseerr):
        request = _make_request(id=1, tvdb_id=12345)
        existing_season = _make_season(request_id=1, season_number=1, synced_at=datetime.now(UTC))
        existing_ep = _make_episode(season_id=1, episode_number=1)

        mock_db.execute.side_effect = [
            MagicMock(scalar_one_or_none=MagicMock(return_value=request)),
            MagicMock(scalar_one_or_none=MagicMock(return_value=existing_season)),
            MagicMock(scalar_one_or_none=MagicMock(return_value=existing_ep)),
        ]
        mock_db.flush = AsyncMock()
        mock_db.commit = AsyncMock()

        mock_overseerr.get_media_details.return_value = TV_DETAILS_NO_EPISODES
        mock_overseerr.get_season_details.return_value = {
            "seasonNumber": 1,
            "episodes": [
                {"episodeNumber": 1, "title": "Pilot", "airDate": "2024-01-01"},
            ],
        }

        seasons = await service.sync_episodes(1)

        assert len(seasons) == 1
        mock_db.add.assert_not_called()
        assert existing_season.synced_at is not None
        mock_db.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_sync_skips_specials_season_zero(self, service, mock_db, mock_overseerr):
        request = _make_request(id=1, tvdb_id=12345)

        req_result = MagicMock()
        req_result.scalar_one_or_none.return_value = request
        season_result = MagicMock()
        season_result.scalar_one_or_none.return_value = None
        ep1_result = MagicMock()
        ep1_result.scalar_one_or_none.return_value = None
        ep2_result = MagicMock()
        ep2_result.scalar_one_or_none.return_value = None
        mock_db.execute.side_effect = [req_result, season_result, ep1_result, ep2_result]
        mock_db.flush = AsyncMock()
        mock_db.commit = AsyncMock()

        mock_overseerr.get_media_details.return_value = TV_DETAILS_NO_EPISODES
        mock_overseerr.get_season_details.return_value = SEASON_1_DETAILS

        seasons = await service.sync_episodes(1)

        assert len(seasons) == 1
        assert seasons[0].season_number == 1
        mock_overseerr.get_season_details.assert_awaited_once_with(79744, 1)

    @pytest.mark.asyncio
    async def test_sync_returns_empty_for_missing_request(self, service, mock_db):
        mock_db.execute.return_value = MagicMock(scalar_one_or_none=MagicMock(return_value=None))
        seasons = await service.sync_episodes(999)
        assert seasons == []

    @pytest.mark.asyncio
    async def test_sync_returns_empty_for_non_tv_request(self, service, mock_db):
        request = _make_request(id=1, media_type=MediaType.MOVIE)
        mock_db.execute.return_value = MagicMock(scalar_one_or_none=MagicMock(return_value=request))
        seasons = await service.sync_episodes(1)
        assert seasons == []

    @pytest.mark.asyncio
    async def test_sync_returns_empty_for_no_external_id(self, service, mock_db):
        request = _make_request(id=1, tvdb_id=None, tmdb_id=None)
        mock_db.execute.return_value = MagicMock(scalar_one_or_none=MagicMock(return_value=request))
        seasons = await service.sync_episodes(1)
        assert seasons == []

    @pytest.mark.asyncio
    async def test_sync_returns_empty_for_tvdb_id_only(self, service, mock_db):
        request = _make_request(id=1, tvdb_id=12345, tmdb_id=None)
        mock_db.execute.return_value = MagicMock(scalar_one_or_none=MagicMock(return_value=request))
        seasons = await service.sync_episodes(1)
        assert seasons == []

    @pytest.mark.asyncio
    async def test_sync_uses_get_season_details_for_episodes(
        self, service, mock_db, mock_overseerr
    ):
        request = _make_request(id=1, tmdb_id=71527, tvdb_id=None)

        req_result = MagicMock()
        req_result.scalar_one_or_none.return_value = request
        season_result = MagicMock()
        season_result.scalar_one_or_none.return_value = None
        ep1_result = MagicMock()
        ep1_result.scalar_one_or_none.return_value = None
        ep2_result = MagicMock()
        ep2_result.scalar_one_or_none.return_value = None
        mock_db.execute.side_effect = [req_result, season_result, ep1_result, ep2_result]
        mock_db.flush = AsyncMock()
        mock_db.commit = AsyncMock()

        mock_overseerr.get_media_details.return_value = {
            "seasons": [{"seasonNumber": 1, "name": "Season 1"}]
        }
        mock_overseerr.get_season_details.return_value = SEASON_1_DETAILS

        seasons = await service.sync_episodes(1)

        mock_overseerr.get_season_details.assert_awaited_once_with(71527, 1)
        assert len(seasons) == 1

    @pytest.mark.asyncio
    async def test_sync_handles_missing_season_details_gracefully(
        self, service, mock_db, mock_overseerr
    ):
        request = _make_request(id=1, tvdb_id=12345)

        req_result = MagicMock()
        req_result.scalar_one_or_none.return_value = request
        season_result = MagicMock()
        season_result.scalar_one_or_none.return_value = None
        mock_db.execute.side_effect = [req_result, season_result]
        mock_db.flush = AsyncMock()
        mock_db.commit = AsyncMock()

        mock_overseerr.get_media_details.return_value = TV_DETAILS_NO_EPISODES
        mock_overseerr.get_season_details.return_value = None

        seasons = await service.sync_episodes(1)

        assert len(seasons) == 1
        mock_db.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_refresh_if_stale_triggers_sync_when_no_seasons(self, service, mock_db):
        mock_db.execute.return_value = MagicMock(
            scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))
        )

        with patch.object(service, "sync_episodes", new_callable=AsyncMock) as mock_sync:
            mock_sync.return_value = [_make_season()]
            seasons = await service.refresh_if_stale(1)
            mock_sync.assert_awaited_once_with(1)
            assert len(seasons) == 1

    @pytest.mark.asyncio
    async def test_refresh_if_stale_skips_when_fresh(self, service, mock_db):
        fresh_season = _make_season(synced_at=datetime.now(UTC))
        mock_db.execute.return_value = MagicMock(
            scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[fresh_season])))
        )

        with patch.object(service, "sync_episodes", new_callable=AsyncMock) as mock_sync:
            seasons = await service.refresh_if_stale(1)
            mock_sync.assert_not_awaited()
            assert len(seasons) == 1

    @pytest.mark.asyncio
    async def test_refresh_if_stale_triggers_sync_when_stale(self, service, mock_db):
        stale_time = datetime.now(UTC) - timedelta(hours=48)
        stale_season = _make_season(synced_at=stale_time)
        mock_db.execute.return_value = MagicMock(
            scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[stale_season])))
        )

        with patch.object(service, "sync_episodes", new_callable=AsyncMock) as mock_sync:
            mock_sync.return_value = [_make_season()]
            await service.refresh_if_stale(1)
            mock_sync.assert_awaited_once_with(1)

    @pytest.mark.asyncio
    async def test_refresh_if_stale_triggers_sync_when_no_synced_at(self, service, mock_db):
        season_no_sync = _make_season(synced_at=None)
        mock_db.execute.return_value = MagicMock(
            scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[season_no_sync])))
        )

        with patch.object(service, "sync_episodes", new_callable=AsyncMock) as mock_sync:
            mock_sync.return_value = [_make_season()]
            await service.refresh_if_stale(1)
            mock_sync.assert_awaited_once_with(1)
