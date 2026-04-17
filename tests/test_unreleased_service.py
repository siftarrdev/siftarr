"""Tests for UnreleasedEvaluator."""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.siftarr.models._base import Base
from app.siftarr.models.episode import Episode
from app.siftarr.models.request import MediaType, Request, RequestStatus
from app.siftarr.models.season import Season
from app.siftarr.services.unreleased_service import UnreleasedEvaluator


@pytest_asyncio.fixture
async def session():
    """Provide an in-memory SQLite AsyncSession with schema created."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    async with session_maker() as db:
        yield db
    await engine.dispose()


@pytest.fixture
def overseerr():
    """Fresh mocked OverseerrService with an AsyncMock `get_media_details`."""
    svc = AsyncMock()
    svc.get_media_details = AsyncMock()
    return svc


async def _make_request(
    db,
    *,
    media_type: MediaType = MediaType.MOVIE,
    status: RequestStatus = RequestStatus.PENDING,
    tmdb_id: int | None = 123,
    external_id: str = "ext-1",
) -> Request:
    req = Request(
        external_id=external_id,
        media_type=media_type,
        tmdb_id=tmdb_id,
        title="Test Title",
        status=status,
    )
    db.add(req)
    await db.commit()
    await db.refresh(req)
    return req


@pytest.mark.asyncio
async def test_evaluate_movie_future_release_returns_unreleased(session, overseerr):
    """A movie with a future releaseDate and no past release_dates should be unreleased."""
    future = (date.today() + timedelta(days=30)).isoformat()
    overseerr.get_media_details.return_value = {
        "status": "Post Production",
        "releaseDate": future,
        "releases": {"results": []},
    }
    req = await _make_request(session)
    evaluator = UnreleasedEvaluator(session, overseerr)

    verdict = await evaluator.evaluate(req)

    assert verdict == "unreleased"
    overseerr.get_media_details.assert_awaited_once_with("movie", 123)


@pytest.mark.asyncio
async def test_evaluate_movie_released_returns_released(session, overseerr):
    """A movie with status=Released should evaluate as released."""
    past = (date.today() - timedelta(days=30)).isoformat()
    overseerr.get_media_details.return_value = {
        "status": "Released",
        "releaseDate": past,
        "releases": {"results": []},
    }
    req = await _make_request(session)
    evaluator = UnreleasedEvaluator(session, overseerr)

    verdict = await evaluator.evaluate(req)

    assert verdict == "released"


@pytest.mark.asyncio
async def test_evaluate_movie_missing_tmdb_id_returns_released(session, overseerr):
    """Missing tmdb_id on a movie request means we cannot evaluate; fail-open."""
    req = await _make_request(session, tmdb_id=None)
    evaluator = UnreleasedEvaluator(session, overseerr)

    verdict = await evaluator.evaluate(req)

    assert verdict == "released"
    overseerr.get_media_details.assert_not_awaited()


@pytest.mark.asyncio
async def test_evaluate_tv_all_aired_downloaded_returns_unreleased(session, overseerr):
    """TV with all aired episodes already downloaded + future episodes remaining → unreleased."""
    today = date.today()
    overseerr.get_media_details.return_value = {
        "status": "Returning Series",
        "firstAirDate": (today - timedelta(days=100)).isoformat(),
    }
    req = await _make_request(
        session, media_type=MediaType.TV, status=RequestStatus.PENDING, external_id="tv-1"
    )

    season = Season(request_id=req.id, season_number=1, status=RequestStatus.PENDING)
    session.add(season)
    await session.commit()
    await session.refresh(season)

    # Two aired+downloaded, one future.
    ep1 = Episode(
        season_id=season.id,
        episode_number=1,
        air_date=today - timedelta(days=14),
        status=RequestStatus.COMPLETED,
    )
    ep2 = Episode(
        season_id=season.id,
        episode_number=2,
        air_date=today - timedelta(days=7),
        status=RequestStatus.AVAILABLE,
    )
    ep3 = Episode(
        season_id=season.id,
        episode_number=3,
        air_date=today + timedelta(days=7),
        status=RequestStatus.RECEIVED,
    )
    session.add_all([ep1, ep2, ep3])
    await session.commit()

    evaluator = UnreleasedEvaluator(session, overseerr)
    verdict = await evaluator.evaluate(req)

    assert verdict == "unreleased"
    overseerr.get_media_details.assert_awaited_once_with("tv", 123)


@pytest.mark.asyncio
async def test_apply_verdict_pending_to_unreleased_transitions(session, overseerr):
    """verdict=unreleased on a PENDING request should transition to UNRELEASED."""
    req = await _make_request(session, status=RequestStatus.PENDING)
    evaluator = UnreleasedEvaluator(session, overseerr)

    new_status = await evaluator.apply_verdict(req, "unreleased")

    assert new_status == RequestStatus.UNRELEASED
    await session.refresh(req)
    assert req.status == RequestStatus.UNRELEASED


@pytest.mark.asyncio
async def test_apply_verdict_unreleased_to_released_becomes_pending(session, overseerr):
    """verdict=released on an UNRELEASED request should move it to PENDING."""
    req = await _make_request(session, status=RequestStatus.UNRELEASED)
    evaluator = UnreleasedEvaluator(session, overseerr)

    new_status = await evaluator.apply_verdict(req, "released")

    assert new_status == RequestStatus.PENDING
    await session.refresh(req)
    assert req.status == RequestStatus.PENDING


@pytest.mark.asyncio
async def test_apply_verdict_noop_when_status_already_matches(session, overseerr):
    """verdict=released on an already PENDING request should be a no-op."""
    req = await _make_request(session, status=RequestStatus.PENDING)
    evaluator = UnreleasedEvaluator(session, overseerr)

    result = await evaluator.apply_verdict(req, "released")

    assert result is None
    await session.refresh(req)
    assert req.status == RequestStatus.PENDING


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "terminal_status",
    [RequestStatus.COMPLETED, RequestStatus.FAILED],
)
async def test_apply_verdict_noop_for_terminal_statuses(session, overseerr, terminal_status):
    """Terminal statuses (COMPLETED/FAILED) should never be redirected to UNRELEASED."""
    req = await _make_request(
        session, status=terminal_status, external_id=f"ext-{terminal_status.value}"
    )
    evaluator = UnreleasedEvaluator(session, overseerr)

    result = await evaluator.apply_verdict(req, "unreleased")

    assert result is None
    await session.refresh(req)
    assert req.status == terminal_status


@pytest.mark.asyncio
async def test_evaluate_and_apply_partially_available_tv_future_only_becomes_unreleased(
    session, overseerr
):
    """Future-only remaining TV requests should redirect PARTIALLY_AVAILABLE to UNRELEASED."""
    today = date.today()
    overseerr.get_media_details.return_value = {
        "status": "Returning Series",
        "firstAirDate": (today - timedelta(days=100)).isoformat(),
    }
    req = await _make_request(
        session,
        media_type=MediaType.TV,
        status=RequestStatus.PARTIALLY_AVAILABLE,
        external_id="tv-partial-future-only",
    )

    season = Season(
        request_id=req.id,
        season_number=1,
        status=RequestStatus.PARTIALLY_AVAILABLE,
    )
    session.add(season)
    await session.commit()
    await session.refresh(season)

    session.add_all(
        [
            Episode(
                season_id=season.id,
                episode_number=1,
                air_date=today - timedelta(days=14),
                status=RequestStatus.COMPLETED,
            ),
            Episode(
                season_id=season.id,
                episode_number=2,
                air_date=today - timedelta(days=7),
                status=RequestStatus.AVAILABLE,
            ),
            Episode(
                season_id=season.id,
                episode_number=3,
                air_date=today + timedelta(days=7),
                status=RequestStatus.RECEIVED,
            ),
        ]
    )
    await session.commit()

    evaluator = UnreleasedEvaluator(session, overseerr)

    new_status = await evaluator.evaluate_and_apply(req)

    assert new_status == RequestStatus.UNRELEASED
    await session.refresh(req)
    assert req.status == RequestStatus.UNRELEASED
