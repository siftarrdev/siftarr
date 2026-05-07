"""Tests for mark-available endpoints."""

import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

if sys.version_info < (3, 11):  # noqa: UP036
    pytest.skip("Requires Python 3.11+ for StrEnum", allow_module_level=True)

from app.siftarr.models.request import RequestStatus
from app.siftarr.services.episode_derive import (
    derive_request_status_from_episodes,
    derive_season_status,
)


def _make_episode(status: RequestStatus, ep_id: int = 1) -> MagicMock:
    ep = MagicMock()
    ep.id = ep_id
    ep.status = status
    return ep


def _make_season(
    episodes: list,
    status: RequestStatus = RequestStatus.PENDING,
    request_id: int = 1,
    season_id: int = 1,
) -> MagicMock:
    s = MagicMock()
    s.id = season_id
    s.request_id = request_id
    s.status = status
    s.episodes = episodes
    return s


def _make_request(
    seasons: list, status: RequestStatus = RequestStatus.PENDING, request_id: int = 1
) -> MagicMock:
    r = MagicMock()
    r.id = request_id
    r.status = status
    r.seasons = seasons
    return r


class TestDeriveSeasonStatus:
    def test_all_completed(self):
        eps = [_make_episode(RequestStatus.COMPLETED), _make_episode(RequestStatus.COMPLETED, 2)]
        assert derive_season_status(eps) == RequestStatus.COMPLETED

    def test_mixed_completed_and_pending(self):
        eps = [_make_episode(RequestStatus.COMPLETED), _make_episode(RequestStatus.PENDING, 2)]
        assert derive_season_status(eps) == RequestStatus.PENDING

    def test_downloading_takes_precedence(self):
        eps = [
            _make_episode(RequestStatus.COMPLETED),
            _make_episode(RequestStatus.DOWNLOADING, 2),
        ]
        assert derive_season_status(eps) == RequestStatus.DOWNLOADING

    def test_staged_takes_precedence_over_completed(self):
        eps = [
            _make_episode(RequestStatus.COMPLETED),
            _make_episode(RequestStatus.STAGED, 2),
        ]
        assert derive_season_status(eps) == RequestStatus.STAGED

    def test_no_episodes_returns_pending(self):
        assert derive_season_status([]) == RequestStatus.PENDING

    def test_unreleased_episodes(self):
        from datetime import date, timedelta

        ep1 = _make_episode(RequestStatus.UNRELEASED)
        ep1.air_date = date.today() + timedelta(days=30)
        ep2 = _make_episode(RequestStatus.UNRELEASED, 2)
        ep2.air_date = date.today() + timedelta(days=60)
        assert derive_season_status([ep1, ep2]) == RequestStatus.UNRELEASED


class TestDeriveRequestStatusFromEpisodes:
    def test_all_seasons_completed(self):
        eps = [_make_episode(RequestStatus.COMPLETED), _make_episode(RequestStatus.COMPLETED, 2)]
        assert derive_request_status_from_episodes(eps) == RequestStatus.COMPLETED

    def test_mixed_episodes(self):
        eps = [_make_episode(RequestStatus.COMPLETED), _make_episode(RequestStatus.PENDING, 2)]
        assert derive_request_status_from_episodes(eps) == RequestStatus.PENDING

    def test_no_episodes(self):
        assert derive_request_status_from_episodes([]) == RequestStatus.PENDING

    def test_pending_episodes(self):
        eps = [_make_episode(RequestStatus.PENDING), _make_episode(RequestStatus.PENDING, 2)]
        assert derive_request_status_from_episodes(eps) == RequestStatus.PENDING

    def test_downloading_takes_precedence(self):
        eps = [
            _make_episode(RequestStatus.COMPLETED),
            _make_episode(RequestStatus.DOWNLOADING, 2),
        ]
        assert derive_request_status_from_episodes(eps) == RequestStatus.DOWNLOADING

    def test_staged_takes_precedence(self):
        eps = [
            _make_episode(RequestStatus.COMPLETED),
            _make_episode(RequestStatus.STAGED, 2),
        ]
        assert derive_request_status_from_episodes(eps) == RequestStatus.STAGED


class TestMarkEpisodeAvailableEndpoint:
    """Integration-style tests using mocked DB for the mark-available endpoint logic."""

    @pytest.mark.asyncio
    async def test_already_completed_raises_400(self):
        """Marking an already-completed episode should raise HTTPException(400)."""
        from fastapi import HTTPException

        from app.siftarr.routers.dashboard_actions import mark_episode_available

        ep = MagicMock()
        ep.id = 1
        ep.status = RequestStatus.COMPLETED
        ep.season_id = 10

        season = MagicMock()
        season.id = 10
        season.request_id = 1

        db = AsyncMock()
        # First call returns episode, second returns season
        ep_result = MagicMock()
        ep_result.scalar_one_or_none.return_value = ep
        season_result = MagicMock()
        season_result.scalar_one_or_none.return_value = season
        db.execute = AsyncMock(side_effect=[ep_result, season_result])

        with pytest.raises(HTTPException) as exc_info:
            await mark_episode_available(request_id=1, episode_id=1, db=db)
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_episode_not_found_raises_404(self):
        from fastapi import HTTPException

        from app.siftarr.routers.dashboard_actions import mark_episode_available

        db = AsyncMock()
        result = MagicMock()
        result.scalar_one_or_none.return_value = None
        db.execute = AsyncMock(return_value=result)

        with pytest.raises(HTTPException) as exc_info:
            await mark_episode_available(request_id=1, episode_id=999, db=db)
        assert exc_info.value.status_code == 404
