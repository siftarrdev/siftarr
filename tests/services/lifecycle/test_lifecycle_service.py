"""Tests for LifecycleService."""

from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from app.siftarr.models.activity_log import ActivityLog
from app.siftarr.models.episode import Episode
from app.siftarr.models.request import MediaType, Request, RequestStatus
from app.siftarr.services.lifecycle.episode_derive import derive_request_status_from_episodes
from app.siftarr.services.lifecycle.lifecycle_service import LifecycleService
from app.siftarr.services.lifecycle.unreleased_service import UnreleasedEvaluator, is_unreleased
from tests.factories import make_episode, make_request, make_season

S = RequestStatus


async def _reload_request(db, request_id):
    query = (
        select(Request).where(Request.id == request_id).execution_options(populate_existing=True)
    )
    return (await db.execute(query)).scalar_one()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("current", "target"),
    [
        pytest.param(S.PENDING, S.SEARCHING, id="pending-to-searching"),
        pytest.param(S.SEARCHING, S.PENDING, id="searching-to-pending"),
        pytest.param(S.STAGED, S.DOWNLOADING, id="staged-to-downloading"),
        pytest.param(S.DOWNLOADING, S.COMPLETED, id="downloading-to-completed"),
        pytest.param(S.SEARCHING, S.FAILED, id="searching-to-failed"),
    ],
)
async def test_transition_updates_status_and_logs_activity(db, current, target):
    request = await make_request(db, status=current)
    await db.commit()

    result = await LifecycleService(db).transition(request.id, target)

    assert result is not None
    assert result.status == target
    stored = await db.get(Request, request.id)
    assert stored is not None
    assert stored.status == target
    logs = (await db.execute(select(ActivityLog))).scalars().all()
    assert len(logs) == 1


@pytest.mark.asyncio
async def test_transition_and_metadata_update_return_none_for_missing_request(db):
    service = LifecycleService(db)

    assert await service.transition(999, S.SEARCHING) is None
    assert await service.update_request_metadata(999, title="New Title") is None
    assert (await db.execute(select(ActivityLog))).scalars().all() == []


@pytest.mark.asyncio
async def test_update_request_metadata_persists_changes(db):
    request = await make_request(db, title="Old Title", year=2020)
    await db.commit()

    result = await LifecycleService(db).update_request_metadata(
        request.id, title="New Title", year=2024
    )

    assert result is not None
    assert (result.title, result.year) == ("New Title", 2024)
    stored = await _reload_request(db, request.id)
    assert (stored.title, stored.year) == ("New Title", 2024)


@pytest.mark.asyncio
async def test_get_active_requests_and_by_status_filter_on_status(db):
    searching = await make_request(db, external_id="1", status=S.SEARCHING)
    pending = await make_request(db, external_id="2", status=S.PENDING)
    await make_request(db, external_id="3", status=S.COMPLETED)
    await make_request(db, external_id="4", status=S.DENIED)
    await db.commit()
    service = LifecycleService(db)

    active = await service.get_active_requests()
    assert {r.id for r in active} == {searching.id, pending.id}

    by_status = await service.get_requests_by_status(S.PENDING)
    assert [r.id for r in by_status] == [pending.id]


TODAY = date(2026, 4, 17)
TV = {"firstAirDate": "2025-01-01", "status": "Returning Series"}
MOVIE_IN_PRODUCTION = {
    "status": "In Production",
    "releaseDate": "2026-08-01",
    "releases": {"results": []},
}


def _eps(*items):
    return [SimpleNamespace(air_date=air_date, status=status) for air_date, status in items]


@pytest.mark.parametrize(
    ("media_type", "tmdb_id", "details", "episodes", "extra", "expected"),
    [
        pytest.param("movie", 123, MOVIE_IN_PRODUCTION, None, {}, True, id="movie-future-release"),
        pytest.param("movie", None, MOVIE_IN_PRODUCTION, None, {}, False, id="no-tmdb-id"),
        pytest.param(
            "tv",
            456,
            TV,
            _eps(
                (date(2026, 4, 1), S.COMPLETED),
                (date(2026, 4, 8), S.COMPLETED),
                (date(2026, 5, 1), S.UNRELEASED),
            ),
            {},
            True,
            id="aired-complete-future-remaining",
        ),
        pytest.param(
            "tv",
            456,
            TV,
            _eps(
                (date(2026, 3, 20), S.COMPLETED),
                (date(2026, 3, 27), S.COMPLETED),
                (None, S.UNRELEASED),
            ),
            {},
            True,
            id="undated-placeholder-after-aired",
        ),
        pytest.param(
            "tv",
            456,
            TV,
            _eps(
                (date(2026, 3, 20), S.COMPLETED),
                (None, S.PENDING),
                (date(2026, 4, 3), S.COMPLETED),
                (date(2026, 5, 1), S.UNRELEASED),
            ),
            {},
            False,
            id="undated-pending-between-aired-needs-release",
        ),
        pytest.param(
            "tv",
            456,
            TV,
            _eps((date(2026, 4, 1), S.COMPLETED), (date(2026, 4, 8), S.COMPLETED)),
            {"has_empty_seasons": True},
            True,
            id="aired-complete-empty-future-season",
        ),
        pytest.param(
            "tv",
            456,
            TV,
            _eps((date(2026, 4, 1), S.COMPLETED), (date(2026, 4, 8), S.COMPLETED)),
            {"has_empty_seasons": False},
            False,
            id="aired-complete-no-empty-seasons",
        ),
        pytest.param(
            "tv",
            456,
            TV | {"nextEpisodeToAir": {"airDate": "2026-05-01"}},
            _eps((date(2026, 4, 1), S.COMPLETED), (date(2026, 4, 8), S.COMPLETED)),
            {},
            True,
            id="next-episode-signal",
        ),
    ],
)
def test_is_unreleased(media_type, tmdb_id, details, episodes, extra, expected):
    request = SimpleNamespace(media_type=media_type, tmdb_id=tmdb_id)

    result = is_unreleased(
        request, media_details=details, local_episodes=episodes, today=TODAY, **extra
    )

    assert result is expected


async def _episode_statuses(db, season_id):
    result = await db.execute(
        select(Episode).where(Episode.season_id == season_id).order_by(Episode.episode_number)
    )
    return [ep.status for ep in result.scalars().all()]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("coverage_title", "expected_season_one", "expected_season_two"),
    [
        pytest.param(
            "Lifecycle.Show.S01E01.1080p.WEB-DL",
            [S.DOWNLOADING, S.PENDING, S.PENDING],
            [S.PENDING],
            id="single-episode",
        ),
        pytest.param(
            "Lifecycle.Show.S01.1080p.WEB-DL",
            [S.DOWNLOADING] * 3,
            [S.PENDING],
            id="season-pack",
        ),
        pytest.param(
            "Lifecycle.Show.S01-S02.1080p.WEB-DL",
            [S.DOWNLOADING] * 3,
            [S.DOWNLOADING],
            id="multi-season-pack",
        ),
        pytest.param(None, [S.DOWNLOADING] * 3, [S.DOWNLOADING], id="no-coverage-is-bulk"),
    ],
)
async def test_transition_downloading_scope(
    db, coverage_title, expected_season_one, expected_season_two
):
    """DOWNLOADING transitions honour release coverage, and stay bulk without it."""
    request = await make_request(db, media_type=MediaType.TV, status=S.STAGED)
    season_one = await make_season(db, request, 1, episodes=3)
    season_two = await make_season(db, request, 2, episodes=1)
    await db.commit()
    request_id, season_one_id, season_two_id = request.id, season_one.id, season_two.id

    await LifecycleService(db).transition(request_id, S.DOWNLOADING, coverage_title=coverage_title)

    assert await _episode_statuses(db, season_one_id) == expected_season_one
    assert await _episode_statuses(db, season_two_id) == expected_season_two

    refreshed = await _reload_request(db, request_id)
    all_episodes = list((await db.execute(select(Episode))).scalars().all())
    assert refreshed.status == derive_request_status_from_episodes(all_episodes)


@pytest.mark.asyncio
async def test_released_verdict_keeps_future_and_placeholder_episodes_unreleased(db):
    """A released verdict reopens newly aired episodes only, not future or placeholder ones."""
    today = date.today()
    request = await make_request(
        db, media_type=MediaType.TV, tmdb_id=456, status=S.UNRELEASED, title="Verdict Show"
    )
    season_one = await make_season(db, request, 1, status=S.UNRELEASED)
    season_two = await make_season(db, request, 2, status=S.UNRELEASED)
    await make_episode(db, season_one, 1, air_date=today - timedelta(days=14), status=S.COMPLETED)
    # Aired since the last episode sync.
    await make_episode(db, season_one, 2, air_date=today - timedelta(days=1), status=S.UNRELEASED)
    await make_episode(db, season_one, 3, air_date=today + timedelta(days=6), status=S.UNRELEASED)
    await make_episode(db, season_two, 1, air_date=None, status=S.UNRELEASED)
    await db.commit()

    season_one_id, season_two_id = season_one.id, season_two.id

    new_status = await UnreleasedEvaluator(db, AsyncMock()).evaluate_and_apply(
        request,
        prefetched_media_details={"firstAirDate": "2020-01-01", "status": "Returning Series"},
    )

    assert new_status == S.PENDING
    assert await _episode_statuses(db, season_one_id) == [S.COMPLETED, S.PENDING, S.UNRELEASED]
    assert await _episode_statuses(db, season_two_id) == [S.UNRELEASED]
