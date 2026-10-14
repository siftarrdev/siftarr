"""Tests for the TV completion summary shown in the pending queue."""

from types import SimpleNamespace

from app.siftarr.models.request import RequestStatus
from app.siftarr.services.dashboard.tv_details_service import summarize_tv_completion


def _season(season_id: int, number: int) -> SimpleNamespace:
    return SimpleNamespace(id=season_id, season_number=number)


def _episodes(season_id: int, *statuses: RequestStatus) -> list[SimpleNamespace]:
    return [SimpleNamespace(season_id=season_id, status=status) for status in statuses]


DONE = RequestStatus.COMPLETED
PENDING = RequestStatus.PENDING
UNRELEASED = RequestStatus.UNRELEASED


def test_single_incomplete_season_reports_its_episode_counts():
    seasons = [_season(1, 1), _season(2, 2), _season(3, 3)]
    episodes = [
        *_episodes(1, DONE, DONE),
        *_episodes(2, DONE, DONE),
        *_episodes(3, DONE, DONE, PENDING),
    ]

    summary = summarize_tv_completion(seasons, episodes)

    assert summary is not None
    assert (summary["seasons_complete"], summary["seasons_total"]) == (2, 3)
    assert summary["incomplete_season"] == {
        "season_number": 3,
        "episodes_complete": 2,
        "episodes_total": 3,
    }


def test_multiple_incomplete_seasons_omit_episode_detail():
    seasons = [_season(1, 1), _season(2, 2)]
    episodes = [*_episodes(1, DONE, PENDING), *_episodes(2, PENDING, PENDING)]

    summary = summarize_tv_completion(seasons, episodes)

    assert summary is not None
    assert (summary["seasons_complete"], summary["seasons_total"]) == (0, 2)
    assert summary["incomplete_season"] is None
    assert summary["ratio"] == 0.25


def test_unreleased_episodes_and_future_seasons_are_not_counted():
    seasons = [_season(1, 1), _season(2, 2)]
    episodes = [*_episodes(1, DONE, PENDING, UNRELEASED), *_episodes(2, UNRELEASED)]

    summary = summarize_tv_completion(seasons, episodes)

    assert summary is not None
    assert summary["seasons_total"] == 1
    assert summary["incomplete_season"] == {
        "season_number": 1,
        "episodes_complete": 1,
        "episodes_total": 2,
    }


def test_no_aired_episodes_returns_none():
    assert summarize_tv_completion([], []) is None
    assert summarize_tv_completion([_season(1, 1)], _episodes(1, UNRELEASED)) is None
