"""Tests for EpisodeSyncService."""

import asyncio
from datetime import UTC, date, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.siftarr.models.episode import Episode
from app.siftarr.models.request import MediaType, Request, RequestStatus
from app.siftarr.models.season import Season
from app.siftarr.services.integrations.plex_service import PlexEpisodeAvailabilityResult
from app.siftarr.services.lifecycle.episode_sync_service import (
    EpisodeSyncService,
    persist_episode_availability,
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


@pytest.mark.parametrize(
    "request_kwargs",
    [
        pytest.param(None, id="missing-request"),
        pytest.param({"media_type": MediaType.MOVIE}, id="non-tv"),
        pytest.param({"tvdb_id": None, "tmdb_id": None}, id="no-external-id"),
        pytest.param({"tvdb_id": 12345, "tmdb_id": None}, id="tvdb-only"),
    ],
)
@pytest.mark.asyncio
async def test_sync_returns_empty_when_request_is_not_syncable(request_kwargs):
    db = AsyncMock()
    request = None if request_kwargs is None else _make_request(id=1, **request_kwargs)
    db.execute.return_value = MagicMock(scalar_one_or_none=MagicMock(return_value=request))
    service = EpisodeSyncService(db, overseerr=AsyncMock())

    assert await service.sync_request(1) == []


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

        assert [season.season_number for season in seasons] == [1]  # specials skipped
        assert mock_db.add.call_count == 3
        mock_overseerr.get_season_details.assert_awaited_once_with(79744, 1)
        mock_db.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_sync_keeps_denied_request_out_of_pending(self, service, mock_db, mock_overseerr):
        """New PENDING episodes from a resync must not move a denied request back to pending."""
        request = _make_request(id=1, tvdb_id=12345)
        request.status = RequestStatus.DENIED

        req_result = MagicMock()
        req_result.scalar_one_or_none.return_value = request
        missing = MagicMock()
        missing.scalar_one_or_none.return_value = None

        mock_db.execute.side_effect = [req_result, missing, missing, missing]
        mock_db.flush = AsyncMock()
        mock_db.commit = AsyncMock()

        mock_overseerr.get_media_details.return_value = TV_DETAILS_NO_EPISODES
        mock_overseerr.get_season_details.return_value = SEASON_1_DETAILS

        await service.sync_request(1)

        assert request.status == RequestStatus.DENIED

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

        with patch(
            "app.siftarr.services.lifecycle.episode_sync_service.get_settings"
        ) as mock_get_settings:
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
    @pytest.mark.parametrize("overseerr_season_status", [4, 5], ids=["partial", "available"])
    async def test_sync_from_overseerr_keeps_pending_season_episodes_pending_or_unreleased(
        self, service, mock_db, mock_overseerr, overseerr_season_status
    ):
        """Episode state comes from air dates, not from Overseerr season status."""
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
            "mediaInfo": {"seasons": [{"seasonNumber": 8, "status": overseerr_season_status}]},
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
    async def test_sync_from_overseerr_marks_undated_placeholders_unreleased(
        self, service, mock_db, mock_overseerr
    ):
        """Undated episodes after the last aired episode are placeholders, not search targets."""
        request = _make_request(id=1, tmdb_id=71527)

        req_result = MagicMock()
        req_result.scalar_one_or_none.return_value = request
        missing = MagicMock()
        missing.scalar_one_or_none.return_value = None
        mock_db.execute.side_effect = [req_result] + [missing] * 7
        mock_db.flush = AsyncMock()
        mock_db.commit = AsyncMock()

        mock_overseerr.get_media_details.return_value = {
            "seasons": [
                {
                    "seasonNumber": 1,
                    "episodes": [
                        {"episodeNumber": 1, "airDate": "2024-01-01"},
                        {"episodeNumber": 2, "airDate": None},
                        {"episodeNumber": 3, "airDate": "2024-01-15"},
                        {"episodeNumber": 4, "airDate": None},
                    ],
                },
                {"seasonNumber": 2, "episodes": [{"episodeNumber": 1, "airDate": None}]},
            ],
        }
        added_rows = []
        mock_db.add = MagicMock(side_effect=added_rows.append)

        await service.sync_request(1)

        added_episodes = [row for row in added_rows if isinstance(row, Episode)]
        assert [episode.status for episode in added_episodes] == [
            RequestStatus.PENDING,
            RequestStatus.PENDING,  # undated, but S01E03 aired after it
            RequestStatus.PENDING,
            RequestStatus.UNRELEASED,  # trailing placeholder in the airing season
            RequestStatus.UNRELEASED,  # future-season placeholder
        ]
        assert request.status == RequestStatus.PENDING

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
        plex.get_episode_availability_result.return_value = PlexEpisodeAvailabilityResult(
            availability={(8, 1): True, (8, 2): False, (8, 3): False},
            authoritative=True,
        )

        service = EpisodeSyncService(mock_db, overseerr=mock_overseerr, plex=plex)
        mock_db.flush = AsyncMock()
        mock_db.commit = AsyncMock()

        # Mock _load_season_episodes to return the test episodes directly
        # (avoids needing a full DB query setup for unit tests)
        with patch(
            "app.siftarr.services.lifecycle.episode_sync_service._load_season_episodes",
            new_callable=AsyncMock,
        ) as mock_load:
            mock_load.return_value = [available_episode, pending_episode, future_episode]

            await service._apply_plex_availability(request, [season])

        assert season.status == RequestStatus.PENDING
        assert request.status == RequestStatus.PENDING
        mock_db.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_sync_request_fresh_multi_season_import_keeps_overseerr_state_on_inconclusive_plex(
        self, mock_db, mock_overseerr
    ):
        """Fresh imports should preserve Overseerr state when Plex availability is inconclusive."""
        request = _make_request(id=1, tmdb_id=71527)
        request.title = "Foundation"
        request.plex_rating_key = "show-123"
        request.status = RequestStatus.PENDING

        mock_db.execute.side_effect = [
            MagicMock(scalar_one_or_none=MagicMock(return_value=request)),
            MagicMock(scalar_one_or_none=MagicMock(return_value=None)),
            MagicMock(scalar_one_or_none=MagicMock(return_value=None)),
            MagicMock(scalar_one_or_none=MagicMock(return_value=None)),
            MagicMock(scalar_one_or_none=MagicMock(return_value=None)),
        ]
        mock_db.flush = AsyncMock()
        mock_db.commit = AsyncMock()

        future_day = (datetime.now(UTC) + timedelta(days=7)).date().isoformat()
        mock_overseerr.get_media_details.return_value = {
            "seasons": [
                {
                    "seasonNumber": 1,
                    "episodes": [{"episodeNumber": 1, "title": "Pilot", "airDate": "2024-01-01"}],
                },
                {
                    "seasonNumber": 2,
                    "episodes": [{"episodeNumber": 1, "title": "Future", "airDate": future_day}],
                },
            ]
        }

        plex = AsyncMock()
        plex.get_episode_availability_result.return_value = PlexEpisodeAvailabilityResult(
            availability={}, authoritative=False
        )
        service = EpisodeSyncService(mock_db, overseerr=mock_overseerr, plex=plex)

        seasons = await service.sync_request(1)

        assert [season.season_number for season in seasons] == [1, 2]
        assert [season.status for season in seasons] == [
            RequestStatus.PENDING,
            RequestStatus.UNRELEASED,
        ]

        added_rows = [call.args[0] for call in mock_db.add.call_args_list]
        added_episodes = [row for row in added_rows if isinstance(row, Episode)]
        assert [episode.status for episode in added_episodes] == [
            RequestStatus.PENDING,
            RequestStatus.UNRELEASED,
        ]
        assert request.status == RequestStatus.PENDING
        plex.get_episode_availability_result.assert_awaited_once_with("show-123")
        assert mock_db.commit.await_count == 1

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("rating_key", "show_lookup", "availability", "expect_availability_call"),
        [
            pytest.param(None, (None, True), None, False, id="plex-show-missing"),
            pytest.param(None, (None, False), None, False, id="plex-show-lookup-inconclusive"),
            pytest.param(
                "show-123",
                None,
                PlexEpisodeAvailabilityResult(availability={}, authoritative=False),
                True,
                id="plex-availability-inconclusive",
            ),
        ],
    )
    async def test_sync_request_keeps_overseerr_states_when_plex_is_not_conclusive(
        self,
        mock_db,
        mock_overseerr,
        rating_key,
        show_lookup,
        availability,
        expect_availability_call,
    ):
        request = _make_request(id=1, tmdb_id=71527)
        request.title = "Foundation"
        request.plex_rating_key = rating_key

        mock_db.execute.side_effect = [
            MagicMock(scalar_one_or_none=MagicMock(return_value=request)),
            MagicMock(scalar_one_or_none=MagicMock(return_value=None)),
            MagicMock(scalar_one_or_none=MagicMock(return_value=None)),
        ]
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
        plex.resolve_show_rating_key.return_value = show_lookup
        plex.get_episode_availability_result.return_value = availability
        service = EpisodeSyncService(mock_db, overseerr=mock_overseerr, plex=plex)

        seasons = await service.sync_request(1)

        assert len(seasons) == 1
        added_episode = next(
            call.args[0] for call in mock_db.add.call_args_list if isinstance(call.args[0], Episode)
        )
        assert added_episode.status == RequestStatus.UNRELEASED
        assert request.status == RequestStatus.UNRELEASED
        assert plex.get_episode_availability_result.await_count == int(expect_availability_call)
        assert mock_db.commit.await_count == 1

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("seasons_spec", "expected_episodes", "expected_seasons", "expected_request"),
        [
            pytest.param(
                [(1, [(date(2024, 1, 1), True), (date(2024, 1, 8), True)])],
                [RequestStatus.COMPLETED, RequestStatus.COMPLETED],
                [RequestStatus.COMPLETED],
                RequestStatus.COMPLETED,
                id="all-aired-on-plex",
            ),
            pytest.param(
                [(2, [(date(2024, 1, 1), True), (date.max, False)])],
                [RequestStatus.COMPLETED, RequestStatus.UNRELEASED],
                [RequestStatus.UNRELEASED],
                RequestStatus.UNRELEASED,
                id="future-only-remainder",
            ),
            pytest.param(
                [(1, [(date(2024, 1, 1), True)]), (2, [(date.max, False)])],
                [RequestStatus.COMPLETED, RequestStatus.UNRELEASED],
                [RequestStatus.COMPLETED, RequestStatus.UNRELEASED],
                RequestStatus.UNRELEASED,
                id="ongoing-show",
            ),
        ],
    )
    async def test_persist_episode_availability_from_plex(
        self,
        mock_db,
        mock_overseerr,
        seasons_spec,
        expected_episodes,
        expected_seasons,
        expected_request,
    ):
        request = _make_request(id=1)
        request.status = RequestStatus.DOWNLOADING
        seasons, episodes_by_season, availability = [], [], {}
        for number, eps in seasons_spec:
            season = _make_season(season_number=number)
            seasons.append(season)
            season_eps = []
            for index, (air_date, on_plex) in enumerate(eps, start=1):
                episode = _make_episode(season_id=season.id, episode_number=index)
                episode.air_date = air_date
                season_eps.append(episode)
                availability[(number, index)] = on_plex
            episodes_by_season.append(season_eps)

        mock_db.flush = AsyncMock()
        mock_db.commit = AsyncMock()

        with patch(
            "app.siftarr.services.lifecycle.episode_sync_service._load_season_episodes",
            new_callable=AsyncMock,
        ) as mock_load:
            mock_load.side_effect = episodes_by_season
            await persist_episode_availability(mock_db, request, seasons, availability)

        assert [ep.status for eps in episodes_by_season for ep in eps] == expected_episodes
        assert [season.status for season in seasons] == expected_seasons
        assert request.status == expected_request
        mock_db.commit.assert_awaited_once()
