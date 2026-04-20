"""Tests for mark-available endpoints."""

import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

if sys.version_info < (3, 11):  # noqa: UP036
    pytest.skip("Requires Python 3.11+ for StrEnum", allow_module_level=True)

from app.siftarr.models.request import RequestStatus
from app.siftarr.routers.dashboard_actions import (
    _recalculate_request_status,
    _recalculate_season_status,
)


def _make_episode(status: RequestStatus, ep_id: int = 1) -> MagicMock:
    ep = MagicMock()
    ep.id = ep_id
    ep.status = status
    return ep


def _make_season(
    episodes: list,
    status: RequestStatus = RequestStatus.RECEIVED,
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
    seasons: list, status: RequestStatus = RequestStatus.RECEIVED, request_id: int = 1
) -> MagicMock:
    r = MagicMock()
    r.id = request_id
    r.status = status
    r.seasons = seasons
    return r


class TestRecalculateSeasonStatus:
    def test_all_available(self):
        eps = [_make_episode(RequestStatus.AVAILABLE), _make_episode(RequestStatus.AVAILABLE, 2)]
        season = _make_season(eps)
        assert _recalculate_season_status(season) == RequestStatus.AVAILABLE

    def test_mixed_available_and_completed(self):
        eps = [_make_episode(RequestStatus.AVAILABLE), _make_episode(RequestStatus.COMPLETED, 2)]
        season = _make_season(eps)
        assert _recalculate_season_status(season) == RequestStatus.AVAILABLE

    def test_partially_available(self):
        eps = [_make_episode(RequestStatus.AVAILABLE), _make_episode(RequestStatus.RECEIVED, 2)]
        season = _make_season(eps)
        assert _recalculate_season_status(season) == RequestStatus.PARTIALLY_AVAILABLE

    def test_none_available(self):
        eps = [_make_episode(RequestStatus.RECEIVED), _make_episode(RequestStatus.RECEIVED, 2)]
        season = _make_season(eps, status=RequestStatus.DOWNLOADING)
        assert _recalculate_season_status(season) == RequestStatus.DOWNLOADING


class TestRecalculateRequestStatus:
    def test_all_seasons_available(self):
        s1 = _make_season([], status=RequestStatus.AVAILABLE)
        s2 = _make_season([], status=RequestStatus.AVAILABLE, season_id=2)
        req = _make_request([s1, s2])
        assert _recalculate_request_status(req) == RequestStatus.AVAILABLE

    def test_mixed_seasons(self):
        s1 = _make_season([], status=RequestStatus.AVAILABLE)
        s2 = _make_season([], status=RequestStatus.RECEIVED, season_id=2)
        req = _make_request([s1, s2])
        assert _recalculate_request_status(req) == RequestStatus.PARTIALLY_AVAILABLE

    def test_no_seasons(self):
        req = _make_request([], status=RequestStatus.PENDING)
        assert _recalculate_request_status(req) == RequestStatus.PENDING

    def test_partially_available_season(self):
        s1 = _make_season([], status=RequestStatus.PARTIALLY_AVAILABLE)
        s2 = _make_season([], status=RequestStatus.RECEIVED, season_id=2)
        req = _make_request([s1, s2])
        assert _recalculate_request_status(req) == RequestStatus.PARTIALLY_AVAILABLE


class TestMarkEpisodeAvailableEndpoint:
    """Integration-style tests using mocked DB for the mark-available endpoint logic."""

    @pytest.mark.asyncio
    async def test_already_available_raises_400(self):
        """Marking an already-available episode should raise HTTPException(400)."""
        from fastapi import HTTPException

        from app.siftarr.routers.dashboard_actions import mark_episode_available

        ep = MagicMock()
        ep.id = 1
        ep.status = RequestStatus.AVAILABLE
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
