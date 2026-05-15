"""Focused tests for dashboard detail release controls."""

from datetime import date, datetime
from typing import cast
from unittest.mock import MagicMock

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.siftarr.models._base import Base
from app.siftarr.models.episode import Episode
from app.siftarr.models.release import Release
from app.siftarr.models.request import MediaType, Request, RequestStatus
from app.siftarr.models.season import Season
from app.siftarr.services.dashboard.dashboard_service import (
    DashboardRequestSummary,
    RequestDetailsData,
    serialize_request_details_response,
)
from app.siftarr.services.dashboard.detail_service import DetailReleaseControls, DetailService
from app.siftarr.services.dashboard.tv_enrichment_service import TVEnrichmentService


@pytest.fixture
async def db_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    async with session_maker() as session:
        yield session
    await engine.dispose()


def _release(title: str, *, size: int, resolution: str, seeders: int = 1, score: int = 10):
    return Release(
        request_id=1,
        title=title,
        size=size,
        seeders=seeders,
        leechers=0,
        download_url="https://example.test/download",
        indexer="Idx",
        publish_date=datetime(2026, 1, 1),
        resolution=resolution,
        score=score,
        passed_rules=True,
    )


@pytest.mark.asyncio
async def test_load_stored_releases_sorts_by_size_and_reports_filtered_total(db_session):
    db_session.add_all(
        [
            _release("Big.Movie.2160p", size=30, resolution="2160p"),
            _release("Small.Movie.2160p", size=10, resolution="2160p"),
            _release("Other.Movie.1080p", size=20, resolution="1080p"),
        ]
    )
    await db_session.commit()
    service = DetailService(db_session, settings=MagicMock())

    releases, total, filtered_total = await service._load_serialized_stored_releases(
        1,
        media_type=MediaType.MOVIE,
        controls=DetailReleaseControls.normalize(
            title="Movie", resolution="4k", sort="size", direction="asc"
        ),
    )

    assert [release["title"] for release in releases] == ["Small.Movie.2160p", "Big.Movie.2160p"]
    assert total == 3
    assert filtered_total == 2


def test_detail_release_controls_normalize_invalid_values():
    controls = DetailReleaseControls.normalize(
        title="  test  ", resolution="nonsense", sort="bad", direction="sideways"
    )

    assert controls.title == "test"
    assert controls.resolution == "all"
    assert controls.sort == "score"
    assert controls.direction == "desc"


def test_detail_release_controls_accept_resolution_aliases():
    assert DetailReleaseControls.normalize(resolution="2160").resolution == "2160p"
    assert DetailReleaseControls.normalize(resolution="4K").resolution == "2160p"
    assert DetailReleaseControls.normalize(resolution="1080").resolution == "1080p"


def test_request_details_serializes_cache_search_hints():
    payload = serialize_request_details_response(
        RequestDetailsData(
            request=DashboardRequestSummary(
                id=1, title="Movie", status="pending", media_type="movie"
            ),
            releases=[],
            total_releases=0,
            filtered_total_releases=0,
            has_cached_releases=False,
            auto_search_eligible=True,
        )
    )

    assert payload["has_cached_releases"] is False
    assert payload["auto_search_eligible"] is True


@pytest.mark.asyncio
async def test_tv_details_do_not_overlay_approved_torrent_as_staged(db_session):
    request = Request(
        external_id="tv-approved-no-staged-overlay",
        media_type=MediaType.TV,
        title="Show",
        status=RequestStatus.DOWNLOADING,
    )
    db_session.add(request)
    await db_session.flush()
    season = Season(
        request_id=request.id,
        season_number=1,
        status=RequestStatus.DOWNLOADING,
    )
    db_session.add(season)
    await db_session.flush()
    db_session.add_all(
        [
            Episode(season_id=season.id, episode_number=1, status=RequestStatus.DOWNLOADING),
            Episode(season_id=season.id, episode_number=2, status=RequestStatus.DOWNLOADING),
        ]
    )
    await db_session.commit()

    tv_info = await TVEnrichmentService(db_session).load_tv_info(
        request_id=request.id,
        background_tasks=None,
        releases=[],
        active_staged_torrents=[
            {
                "status": "approved",
                "target_scope": {"type": "season_pack", "season_numbers": [1]},
            }
        ],
    )

    season_payload = tv_info.seasons[0]
    assert season_payload["status"] == "downloading"
    assert season_payload["staged_count"] == 0
    episodes = cast("list[dict[str, object]]", season_payload["episodes"])
    assert [episode["status"] for episode in episodes] == [
        "downloading",
        "downloading",
    ]
    assert tv_info.aggregate_counts.get("staged", 0) == 0


@pytest.mark.asyncio
async def test_tv_details_count_completed_as_available_and_unreleased_separate(db_session):
    request = Request(
        external_id="tv-completed-unreleased-counts",
        media_type=MediaType.TV,
        title="Show",
        status=RequestStatus.UNRELEASED,
    )
    db_session.add(request)
    await db_session.flush()
    season = Season(request_id=request.id, season_number=1, status=RequestStatus.UNRELEASED)
    db_session.add(season)
    await db_session.flush()
    db_session.add_all(
        [
            Episode(season_id=season.id, episode_number=1, status=RequestStatus.COMPLETED),
            Episode(
                season_id=season.id,
                episode_number=2,
                status=RequestStatus.UNRELEASED,
                air_date=date(2026, 5, 1),
            ),
        ]
    )
    await db_session.commit()

    tv_info = await TVEnrichmentService(db_session).load_tv_info(
        request_id=request.id,
        background_tasks=None,
        releases=[],
    )

    season_payload = tv_info.seasons[0]
    assert season_payload["available_count"] == 1
    assert season_payload["pending_count"] == 0
    assert season_payload["unreleased_count"] == 1
    assert tv_info.aggregate_counts == {
        "available": 1,
        "pending": 0,
        "unreleased": 1,
        "total": 2,
    }
