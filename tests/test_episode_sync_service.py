"""Tests for EpisodeSyncService."""

from datetime import UTC, date, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.siftarr.models.episode import Episode
from app.siftarr.models.request import MediaType, Request, RequestStatus
from app.siftarr.models.season import Season
from app.siftarr.services.episode_sync_service import (
    EpisodeSyncService,
    _derive_episode_status,
    _derive_request_status_from_seasons,
    _derive_season_status,
)


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
        db = AsyncMock()
        db.add = MagicMock()
        return db

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

    @pytest.mark.asyncio
    async def test_refresh_if_stale_applies_plex_to_fresh_local_data_without_resync(
        self, mock_db, mock_overseerr
    ):
        """Missing Plex keys on fresh data should not force a full Overseerr re-sync."""
        request = _make_request(id=1, overseerr_request_id=55)
        request.plex_rating_key = None
        season = _make_season(synced_at=datetime.now(UTC))
        season.episodes = [_make_episode(season_id=season.id, episode_number=1)]

        request_result = MagicMock()
        request_result.scalar_one_or_none.return_value = request
        seasons_result = MagicMock()
        seasons_result.scalars.return_value.all.return_value = [season]
        mock_db.execute.side_effect = [request_result, seasons_result]

        plex = AsyncMock()
        service = EpisodeSyncService(mock_db, overseerr=mock_overseerr, plex=plex)

        with (
            patch.object(service, "sync_episodes", new_callable=AsyncMock) as mock_sync,
            patch.object(
                service,
                "_apply_plex_to_existing_seasons",
                new_callable=AsyncMock,
            ) as mock_apply,
        ):
            mock_apply.return_value = [season]
            seasons = await service.refresh_if_stale(1)

        mock_sync.assert_not_awaited()
        mock_apply.assert_awaited_once_with(request, [season])
        assert seasons == [season]

    @pytest.mark.asyncio
    async def test_refresh_if_stale_applies_plex_to_fresh_partial_season_without_available_episodes(
        self, mock_db, mock_overseerr
    ):
        """Fresh partial seasons should still trigger Plex enrichment when 0/x are resolved."""
        request = _make_request(id=1, overseerr_request_id=55)
        request.plex_rating_key = "plex-123"
        season = _make_season(synced_at=datetime.now(UTC))
        season.status = RequestStatus.PARTIALLY_AVAILABLE
        episode_one = _make_episode(season_id=season.id, episode_number=1)
        episode_one.status = RequestStatus.PENDING
        episode_two = _make_episode(season_id=season.id, episode_number=2)
        episode_two.status = RequestStatus.UNRELEASED
        season.episodes = [episode_one, episode_two]

        request_result = MagicMock()
        request_result.scalar_one_or_none.return_value = request
        seasons_result = MagicMock()
        seasons_result.scalars.return_value.all.return_value = [season]
        mock_db.execute.side_effect = [request_result, seasons_result]

        plex = AsyncMock()
        service = EpisodeSyncService(mock_db, overseerr=mock_overseerr, plex=plex)

        with (
            patch.object(service, "sync_episodes", new_callable=AsyncMock) as mock_sync,
            patch.object(
                service,
                "_apply_plex_to_existing_seasons",
                new_callable=AsyncMock,
            ) as mock_apply,
        ):
            mock_apply.return_value = [season]
            seasons = await service.refresh_if_stale(1)

        mock_sync.assert_not_awaited()
        mock_apply.assert_awaited_once_with(request, [season])
        assert seasons == [season]

    @pytest.mark.asyncio
    async def test_sync_from_overseerr_keeps_partial_season_episodes_pending_or_unreleased(
        self, service, mock_db, mock_overseerr
    ):
        """Fresh Overseerr-only partial seasons should not stamp episode rows partially_available."""
        request = _make_request(id=1, tmdb_id=71527, overseerr_request_id=55)

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

        future_day = (datetime.now(UTC) + timedelta(days=7)).date().isoformat()
        mock_overseerr.get_media_details.return_value = {
            "mediaInfo": {"seasons": [{"seasonNumber": 8, "status": 4}]},
            "seasons": [
                {
                    "seasonNumber": 8,
                    "episodes": [
                        {"episodeNumber": 1, "title": "Episode 1", "airDate": "2024-01-01"},
                        {"episodeNumber": 2, "title": "Episode 2", "airDate": future_day},
                    ],
                }
            ],
        }

        added_rows = []

        def capture_add(instance):
            added_rows.append(instance)

        mock_db.add = MagicMock(side_effect=capture_add)

        await service.sync_episodes(1)

        added_episodes = [row for row in added_rows if isinstance(row, Episode)]
        assert [episode.status for episode in added_episodes] == [
            RequestStatus.PENDING,
            RequestStatus.UNRELEASED,
        ]

    def test_derive_episode_status_prioritizes_available_then_unreleased(self):
        """Episode status should prefer Plex availability, then future-airing unreleased state."""
        tomorrow = datetime.now(UTC).date() + timedelta(days=1)

        assert _derive_episode_status(is_on_plex=True, air_date=tomorrow) == RequestStatus.AVAILABLE
        assert (
            _derive_episode_status(is_on_plex=False, air_date=tomorrow) == RequestStatus.UNRELEASED
        )
        assert (
            _derive_episode_status(is_on_plex=False, air_date=date(2024, 1, 1))
            == RequestStatus.PENDING
        )

    def test_derive_season_status_keeps_partial_when_available_and_unreleased_mix(self):
        """Mixed available and unreleased episodes should keep the season partially available."""
        episode_one = _make_episode()
        episode_one.status = RequestStatus.AVAILABLE
        episode_two = _make_episode(episode_number=2)
        episode_two.status = RequestStatus.UNRELEASED

        assert (
            _derive_season_status([episode_one, episode_two]) == RequestStatus.PARTIALLY_AVAILABLE
        )

    def test_derive_request_status_from_seasons_supports_partial_and_unreleased(self):
        """Request aggregate status should roll up from season statuses."""
        available = _make_season(season_number=1)
        available.status = RequestStatus.AVAILABLE
        future = _make_season(season_number=2)
        future.status = RequestStatus.UNRELEASED
        pending = _make_season(season_number=3)
        pending.status = RequestStatus.PENDING

        assert _derive_request_status_from_seasons([available]) == RequestStatus.AVAILABLE
        assert _derive_request_status_from_seasons([future]) == RequestStatus.UNRELEASED
        assert (
            _derive_request_status_from_seasons([available, future])
            == RequestStatus.PARTIALLY_AVAILABLE
        )
        assert _derive_request_status_from_seasons([pending, future]) == RequestStatus.PENDING

    @pytest.mark.asyncio
    async def test_apply_fallback_statuses_preserves_unreleased_and_partial_availability(
        self, service
    ):
        """Fallback logic should not flatten future episodes or partial seasons to pending."""
        season = _make_season(season_number=8)
        season.status = RequestStatus.PARTIALLY_AVAILABLE
        available_episode = _make_episode(season_id=season.id, episode_number=1)
        available_episode.status = RequestStatus.AVAILABLE
        future_episode = _make_episode(season_id=season.id, episode_number=16)
        future_episode.air_date = datetime.now(UTC).date() + timedelta(days=7)
        future_episode.status = RequestStatus.PARTIALLY_AVAILABLE
        season.episodes = [available_episode, future_episode]

        await service._apply_fallback_statuses([season])

        assert available_episode.status == RequestStatus.AVAILABLE
        assert future_episode.status == RequestStatus.UNRELEASED
        assert season.status == RequestStatus.PARTIALLY_AVAILABLE

    @pytest.mark.asyncio
    async def test_apply_plex_availability_updates_request_status(self, mock_db, mock_overseerr):
        """Plex-enriched season state should also persist the request aggregate status."""
        request = _make_request(id=1)
        request.title = "The Rookie"
        request.plex_rating_key = "plex-123"
        request.status = RequestStatus.PENDING

        season = _make_season(season_number=8)
        season.status = RequestStatus.PENDING
        available_episode = _make_episode(season_id=season.id, episode_number=1)
        available_episode.air_date = date(2024, 1, 1)
        pending_episode = _make_episode(season_id=season.id, episode_number=2)
        pending_episode.air_date = date(2024, 1, 8)
        future_episode = _make_episode(season_id=season.id, episode_number=3)
        future_episode.air_date = datetime.now(UTC).date() + timedelta(days=7)
        season.episodes = [available_episode, pending_episode, future_episode]

        plex = AsyncMock()
        plex.get_episode_availability.return_value = {(8, 1): True, (8, 2): False, (8, 3): False}

        service = EpisodeSyncService(mock_db, overseerr=mock_overseerr, plex=plex)
        mock_db.flush = AsyncMock()
        mock_db.commit = AsyncMock()

        seasons = await service._apply_plex_availability(request, [season])

        assert seasons == [season]
        assert season.status == RequestStatus.PARTIALLY_AVAILABLE
        assert request.status == RequestStatus.PARTIALLY_AVAILABLE
        mock_db.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_apply_fallback_request_status_updates_request_aggregate(self, service):
        """Overseerr-only fallback should keep request-level aggregate semantics."""
        request = _make_request(id=1)
        request.status = RequestStatus.PENDING
        season = _make_season(season_number=8)
        season.status = RequestStatus.UNRELEASED

        await service._apply_fallback_request_status(request, [season])

        assert request.status == RequestStatus.UNRELEASED
