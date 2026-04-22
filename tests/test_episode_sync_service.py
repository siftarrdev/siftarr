"""Tests for EpisodeSyncService."""

import asyncio
from datetime import UTC, date, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.siftarr.models.episode import Episode
from app.siftarr.models.request import MediaType, Request, RequestStatus
from app.siftarr.models.season import Season
from app.siftarr.services.episode_sync_service import (
    EpisodeSyncService,
    _derive_episode_status,
    _derive_request_status_from_episodes,
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
    season.status = RequestStatus.PENDING
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
    ep.status = RequestStatus.PENDING
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

        seasons = await service.sync_request(1)

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

        seasons = await service.sync_request(1)

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

        seasons = await service.sync_request(1)

        assert len(seasons) == 1
        assert seasons[0].season_number == 1
        mock_overseerr.get_season_details.assert_awaited_once_with(79744, 1)

    @pytest.mark.asyncio
    async def test_sync_returns_empty_for_missing_request(self, service, mock_db):
        mock_db.execute.return_value = MagicMock(scalar_one_or_none=MagicMock(return_value=None))
        seasons = await service.sync_request(999)
        assert seasons == []

    @pytest.mark.asyncio
    async def test_sync_returns_empty_for_non_tv_request(self, service, mock_db):
        request = _make_request(id=1, media_type=MediaType.MOVIE)
        mock_db.execute.return_value = MagicMock(scalar_one_or_none=MagicMock(return_value=request))
        seasons = await service.sync_request(1)
        assert seasons == []

    @pytest.mark.asyncio
    async def test_sync_returns_empty_for_no_external_id(self, service, mock_db):
        request = _make_request(id=1, tvdb_id=None, tmdb_id=None)
        mock_db.execute.return_value = MagicMock(scalar_one_or_none=MagicMock(return_value=request))
        seasons = await service.sync_request(1)
        assert seasons == []

    @pytest.mark.asyncio
    async def test_sync_returns_empty_for_tvdb_id_only(self, service, mock_db):
        request = _make_request(id=1, tvdb_id=12345, tmdb_id=None)
        mock_db.execute.return_value = MagicMock(scalar_one_or_none=MagicMock(return_value=request))
        seasons = await service.sync_request(1)
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

        seasons = await service.sync_request(1)

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

        seasons = await service.sync_request(1)

        assert len(seasons) == 1
        mock_db.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_sync_fetches_missing_season_payloads_with_bounded_concurrency(
        self, mock_db, mock_overseerr
    ):
        request = _make_request(id=1, tmdb_id=71527, tvdb_id=None)
        request.status = RequestStatus.PENDING

        mock_db.execute.side_effect = [
            MagicMock(scalar_one_or_none=MagicMock(return_value=request)),
            MagicMock(scalar_one_or_none=MagicMock(return_value=None)),
            MagicMock(scalar_one_or_none=MagicMock(return_value=None)),
            MagicMock(scalar_one_or_none=MagicMock(return_value=None)),
            MagicMock(scalar_one_or_none=MagicMock(return_value=None)),
            MagicMock(scalar_one_or_none=MagicMock(return_value=None)),
            MagicMock(scalar_one_or_none=MagicMock(return_value=None)),
            MagicMock(scalar_one_or_none=MagicMock(return_value=None)),
            MagicMock(scalar_one_or_none=MagicMock(return_value=None)),
            MagicMock(scalar_one_or_none=MagicMock(return_value=None)),
            MagicMock(scalar_one_or_none=MagicMock(return_value=None)),
            MagicMock(scalar_one_or_none=MagicMock(return_value=None)),
        ]

        next_season_id = 100

        async def flush_side_effect():
            nonlocal next_season_id
            for call in mock_db.add.call_args_list:
                row = call.args[0]
                if isinstance(row, Season) and row.id is None:
                    row.id = next_season_id
                    next_season_id += 1

        mock_db.flush = AsyncMock(side_effect=flush_side_effect)
        mock_db.commit = AsyncMock()

        media_details = {
            "seasons": [
                {"seasonNumber": 0, "name": "Specials"},
                {"seasonNumber": 1, "name": "Season 1"},
                {"seasonNumber": 2, "name": "Season 2"},
                {"seasonNumber": 3, "name": "Season 3"},
            ]
        }
        mock_overseerr.get_media_details.return_value = media_details

        release_details = asyncio.Event()
        first_batch_started = asyncio.Event()
        started_calls: list[int] = []
        in_flight = 0
        max_in_flight = 0

        async def get_season_details_side_effect(tv_id, season_number):
            nonlocal in_flight, max_in_flight
            assert tv_id == 71527
            started_calls.append(season_number)
            in_flight += 1
            max_in_flight = max(max_in_flight, in_flight)
            if in_flight == 2:
                first_batch_started.set()

            await release_details.wait()
            in_flight -= 1

            if season_number == 2:
                raise RuntimeError("season detail failure")

            return {
                "seasonNumber": season_number,
                "episodes": [
                    {
                        "episodeNumber": season_number,
                        "title": f"Episode {season_number}",
                        "airDate": "2024-01-01",
                    }
                ],
            }

        mock_overseerr.get_season_details.side_effect = get_season_details_side_effect

        service = EpisodeSyncService(mock_db, overseerr=mock_overseerr)

        with patch("app.siftarr.services.episode_sync_service.get_settings") as mock_get_settings:
            mock_get_settings.return_value = MagicMock(
                overseerr_sync_concurrency=2,
            )

            sync_task = asyncio.create_task(service.sync_request(1))
            await first_batch_started.wait()

            assert started_calls == [1, 2]
            assert max_in_flight == 2

            release_details.set()
            seasons = await sync_task

        assert [season.season_number for season in seasons] == [1, 2, 3]
        assert started_calls == [1, 2, 3]
        assert max_in_flight == 2
        mock_db.commit.assert_awaited_once()

        added_rows = [call.args[0] for call in mock_db.add.call_args_list]
        added_seasons = [row for row in added_rows if isinstance(row, Season)]
        added_episodes = [row for row in added_rows if isinstance(row, Episode)]

        assert [season.season_number for season in added_seasons] == [1, 2, 3]
        assert [(episode.episode_number, episode.title) for episode in added_episodes] == [
            (1, "Episode 1"),
            (3, "Episode 3"),
        ]
        assert all(episode.status == RequestStatus.PENDING for episode in added_episodes)
        assert request.status == RequestStatus.PENDING

    @pytest.mark.asyncio
    async def test_sync_from_overseerr_keeps_pending_season_episodes_pending_or_unreleased(
        self, service, mock_db, mock_overseerr
    ):
        """Fresh Overseerr-only pending seasons should not stamp episode rows pending."""
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

        await service.sync_request(1)

        added_episodes = [row for row in added_rows if isinstance(row, Episode)]
        assert [episode.status for episode in added_episodes] == [
            RequestStatus.PENDING,
            RequestStatus.UNRELEASED,
        ]

    @pytest.mark.asyncio
    async def test_sync_from_overseerr_ignores_completed_status_for_episode_state(
        self, service, mock_db, mock_overseerr
    ):
        """TV episode state should not be seeded from Overseerr completion."""
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
            "mediaInfo": {"seasons": [{"seasonNumber": 8, "status": 5}]},
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

        await service.sync_request(1)

        added_episodes = [row for row in added_rows if isinstance(row, Episode)]
        assert [episode.status for episode in added_episodes] == [
            RequestStatus.PENDING,
            RequestStatus.UNRELEASED,
        ]

    def test_derive_episode_status_prioritizes_completed_then_unreleased(self):
        """Episode status should prefer Plex completion, then future-airing unreleased state."""
        tomorrow = date.max

        assert _derive_episode_status(is_on_plex=True, air_date=tomorrow) == RequestStatus.COMPLETED
        assert (
            _derive_episode_status(is_on_plex=False, air_date=tomorrow) == RequestStatus.UNRELEASED
        )
        assert (
            _derive_episode_status(is_on_plex=False, air_date=date(2024, 1, 1))
            == RequestStatus.PENDING
        )

    def test_derive_season_status_keeps_pending_when_completed_and_unreleased_mix(self):
        """Mixed completed and unreleased episodes should keep the season pending."""
        episode_one = _make_episode()
        episode_one.status = RequestStatus.COMPLETED
        episode_two = _make_episode(episode_number=2)
        episode_two.status = RequestStatus.UNRELEASED

        assert _derive_season_status([episode_one, episode_two]) == RequestStatus.PENDING

    def test_derive_request_status_from_episodes_supports_pending_and_unreleased(self):
        """Request aggregate status should roll up directly from episode states."""
        available = _make_episode(episode_number=1)
        available.status = RequestStatus.COMPLETED
        future = _make_episode(episode_number=2)
        future.status = RequestStatus.UNRELEASED
        future.air_date = date.max
        pending = _make_episode(episode_number=3)
        pending.status = RequestStatus.PENDING

        assert _derive_request_status_from_episodes([available]) == RequestStatus.COMPLETED
        assert _derive_request_status_from_episodes([future]) == RequestStatus.UNRELEASED
        assert _derive_request_status_from_episodes([available, future]) == RequestStatus.PENDING
        assert _derive_request_status_from_episodes([pending, future]) == RequestStatus.PENDING

    def test_derive_request_status_from_seasons_uses_episode_rollup_when_present(self):
        """Season wrapper should delegate aggregate state to episode statuses."""
        available = _make_season(season_number=1)
        available_episode = _make_episode(season_id=available.id, episode_number=1)
        available_episode.status = RequestStatus.COMPLETED
        available.episodes = [available_episode]

        future = _make_season(season_number=2)
        future_episode = _make_episode(season_id=future.id, episode_number=1)
        future_episode.status = RequestStatus.UNRELEASED
        future_episode.air_date = date.max
        future.episodes = [future_episode]

        assert _derive_request_status_from_seasons([available]) == RequestStatus.COMPLETED
        assert _derive_request_status_from_seasons([future]) == RequestStatus.UNRELEASED
        assert _derive_request_status_from_seasons([available, future]) == RequestStatus.PENDING

    @pytest.mark.asyncio
    async def test_apply_plex_completed_updates_request_status(self, mock_db, mock_overseerr):
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
        future_episode.air_date = date.max

        plex = AsyncMock()
        plex.get_episode_availability.return_value = {(8, 1): True, (8, 2): False, (8, 3): False}

        service = EpisodeSyncService(mock_db, overseerr=mock_overseerr, plex=plex)
        mock_db.flush = AsyncMock()
        mock_db.commit = AsyncMock()

        # Mock _load_season_episodes to return the test episodes directly
        # (avoids needing a full DB query setup for unit tests)
        with patch.object(service, "_load_season_episodes", new_callable=AsyncMock) as mock_load:
            mock_load.return_value = [available_episode, pending_episode, future_episode]

            seasons = await service._apply_plex_availability(request, [season])

        assert seasons == [season]
        assert season.status == RequestStatus.PENDING
        assert request.status == RequestStatus.PENDING
        mock_db.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_reconcile_existing_seasons_marks_all_requested_episodes_completed(
        self, mock_db, mock_overseerr
    ):
        """When all requested aired episodes are on Plex, TV requests should aggregate to completed."""
        request = _make_request(id=1)
        request.status = RequestStatus.DOWNLOADING

        season = _make_season(season_number=1)
        episode_one = _make_episode(season_id=season.id, episode_number=1)
        episode_one.air_date = date(2024, 1, 1)
        episode_two = _make_episode(season_id=season.id, episode_number=2)
        episode_two.air_date = date(2024, 1, 8)

        service = EpisodeSyncService(mock_db, overseerr=mock_overseerr)
        mock_db.flush = AsyncMock()
        mock_db.commit = AsyncMock()

        with patch.object(service, "_load_season_episodes", new_callable=AsyncMock) as mock_load:
            mock_load.return_value = [episode_one, episode_two]

            await service.reconcile_existing_seasons_from_plex(
                request,
                [season],
                {(1, 1): True, (1, 2): True},
            )

        assert episode_one.status == RequestStatus.COMPLETED
        assert episode_two.status == RequestStatus.COMPLETED
        assert season.status == RequestStatus.COMPLETED
        assert request.status == RequestStatus.COMPLETED
        mock_db.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_reconcile_existing_seasons_preserves_pending_season_with_future_episodes(
        self, mock_db, mock_overseerr
    ):
        """Future unreleased episodes should keep the season and request pending."""
        request = _make_request(id=1)
        request.status = RequestStatus.DOWNLOADING

        season = _make_season(season_number=2)
        aired_episode = _make_episode(season_id=season.id, episode_number=1)
        aired_episode.air_date = date(2024, 1, 1)
        future_episode = _make_episode(season_id=season.id, episode_number=2)
        future_episode.air_date = date.max

        service = EpisodeSyncService(mock_db, overseerr=mock_overseerr)
        mock_db.flush = AsyncMock()
        mock_db.commit = AsyncMock()

        with patch.object(service, "_load_season_episodes", new_callable=AsyncMock) as mock_load:
            mock_load.return_value = [aired_episode, future_episode]

            await service.reconcile_existing_seasons_from_plex(
                request,
                [season],
                {(2, 1): True, (2, 2): False},
            )

        assert aired_episode.status == RequestStatus.COMPLETED
        assert future_episode.status == RequestStatus.UNRELEASED
        assert season.status == RequestStatus.PENDING
        assert request.status == RequestStatus.PENDING
        mock_db.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_reconcile_existing_seasons_keeps_ongoing_show_pending(
        self, mock_db, mock_overseerr
    ):
        """Ongoing shows with all currently aired episodes on Plex should not flatten to completed."""
        request = _make_request(id=1)
        request.status = RequestStatus.DOWNLOADING

        available_season = _make_season(season_number=1)
        available_episode = _make_episode(season_id=available_season.id, episode_number=1)
        available_episode.air_date = date(2024, 1, 1)

        future_season = _make_season(season_number=2)
        future_episode = _make_episode(season_id=future_season.id, episode_number=1)
        future_episode.air_date = date.max

        service = EpisodeSyncService(mock_db, overseerr=mock_overseerr)
        mock_db.flush = AsyncMock()
        mock_db.commit = AsyncMock()

        with patch.object(service, "_load_season_episodes", new_callable=AsyncMock) as mock_load:
            mock_load.side_effect = [[available_episode], [future_episode]]

            await service.reconcile_existing_seasons_from_plex(
                request,
                [available_season, future_season],
                {(1, 1): True, (2, 1): False},
            )

        assert available_season.status == RequestStatus.COMPLETED
        assert future_season.status == RequestStatus.UNRELEASED
        assert request.status == RequestStatus.PENDING
        mock_db.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_sync_request_keeps_overseerr_states_when_plex_lookup_missing(
        self, mock_db, mock_overseerr
    ):
        """Missing Plex matches should leave Overseerr-derived episode state intact."""
        request = _make_request(id=1, tmdb_id=71527)
        request.title = "Foundation"
        request.plex_rating_key = None

        req_result = MagicMock()
        req_result.scalar_one_or_none.return_value = request
        season_result = MagicMock()
        season_result.scalar_one_or_none.return_value = None
        episode_result = MagicMock()
        episode_result.scalar_one_or_none.return_value = None
        mock_db.execute.side_effect = [req_result, season_result, episode_result]
        mock_db.flush = AsyncMock()
        mock_db.commit = AsyncMock()

        mock_overseerr.get_media_details.return_value = {
            "seasons": [
                {
                    "seasonNumber": 1,
                    "episodes": [
                        {"episodeNumber": 1, "title": "Pilot", "airDate": date.max.isoformat()}
                    ],
                }
            ]
        }

        plex = AsyncMock()
        plex.get_show_by_tmdb.return_value = None
        plex.get_show_by_tvdb.return_value = None
        plex.search_show.return_value = []
        service = EpisodeSyncService(mock_db, overseerr=mock_overseerr, plex=plex)

        seasons = await service.sync_request(1)

        assert len(seasons) == 1
        added_episode = next(
            call.args[0] for call in mock_db.add.call_args_list if isinstance(call.args[0], Episode)
        )
        assert added_episode.status == RequestStatus.UNRELEASED
        assert request.status == RequestStatus.UNRELEASED
        plex.get_episode_availability.assert_not_awaited()
        assert mock_db.commit.await_count == 1
