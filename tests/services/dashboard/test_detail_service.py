"""Focused tests for dashboard detail release controls."""

from datetime import date, datetime
from typing import cast
from unittest.mock import AsyncMock, MagicMock

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


@pytest.mark.asyncio
async def test_tv_detail_pagination_enriches_only_paginated_releases(db_session, monkeypatch):
    request = Request(
        external_id="tv-paginated-detail",
        media_type=MediaType.TV,
        title="Show",
        status=RequestStatus.PENDING,
    )
    db_session.add(request)
    await db_session.flush()
    season = Season(request_id=request.id, season_number=1, status=RequestStatus.PENDING)
    db_session.add(season)
    await db_session.flush()
    db_session.add(Episode(season_id=season.id, episode_number=1, status=RequestStatus.PENDING))
    for idx in range(3):
        db_session.add(
            Release(
                request_id=request.id,
                title=f"Show.S01E0{idx + 1}.1080p",
                size=1000 + idx,
                seeders=10 - idx,
                leechers=0,
                download_url="https://example.test/download",
                indexer="Idx",
                publish_date=datetime(2026, 1, 1),
                resolution="1080p",
                score=100 - idx,
                passed_rules=True,
                rule_evidence={"passed": True, "score": 100 - idx, "matches": []},
            )
        )
    await db_session.commit()

    serialized_titles: list[str] = []

    def spy_serializer(release, evaluation, *, media_type):
        serialized_titles.append(release.title)
        return {
            "title": release.title,
            "score": release.score,
            "_size_bytes": release.size,
            "seeders": release.seeders,
            "target_scope": {"type": "single_episode", "season_number": 1, "episode_number": 1},
        }

    monkeypatch.setattr(
        "app.siftarr.services.dashboard.detail_service.serialize_stored_evaluated_release",
        spy_serializer,
    )
    monkeypatch.setattr(
        "app.siftarr.services.dashboard.detail_service.MetadataService.load_overseerr_details",
        AsyncMock(return_value=None),
    )
    service = DetailService(db_session, settings=MagicMock())

    details = await service.load_request_details(
        request,
        request_id=request.id,
        background_tasks=MagicMock(),
        offset=0,
        limit=1,
    )

    assert details.filtered_total_releases == 3
    assert len(details.releases) == 1
    assert serialized_titles == ["Show.S01E01.1080p"]
    assert details.tv_info is not None


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


class TestGroupTvReleasesForPackUI:
    """Season-pack grouping contract used by the dashboard packs UI.

    Single-season packs must bucket under their actual season key only, while
    multi-season packs bucket under every covered season.
    """

    def test_single_season_pack_groups_under_its_season(self) -> None:
        from app.siftarr.services.dashboard.tv_enrichment_service import TVEnrichmentService

        service = TVEnrichmentService(MagicMock())
        single_pack: dict[str, object] = {
            "title": "Show.S02.1080p",
            "season_number": 2,
            "episode_number": None,
        }
        multi_pack: dict[str, object] = {
            "title": "Show.S01-S02.1080p",
            "season_number": 1,
            "episode_number": None,
            "covered_seasons": [1, 2],
        }
        episode_release: dict[str, object] = {
            "title": "Show.S01E03.1080p",
            "season_number": 1,
            "episode_number": 3,
        }

        by_season, by_episode = service._group_tv_releases(
            [single_pack, multi_pack, episode_release], [1, 2]
        )

        assert by_season[2] == [single_pack, multi_pack]
        assert by_season[1] == [multi_pack]
        assert by_episode[(1, 3)] == [episode_release]

    def test_duplicate_rows_are_grouped_once(self) -> None:
        """Pre-existing duplicate release rows must not render twice."""
        from app.siftarr.services.dashboard.tv_enrichment_service import TVEnrichmentService

        service = TVEnrichmentService(MagicMock())
        first: dict[str, object] = {
            "title": "Show.S01E03.1080p",
            "info_hash": None,
            "season_number": 1,
            "episode_number": 3,
        }
        duplicate: dict[str, object] = dict(first)
        hashed: dict[str, object] = {
            "title": "Show.S01.1080p",
            "info_hash": "abc123",
            "season_number": 1,
            "episode_number": None,
        }
        hashed_duplicate: dict[str, object] = {**hashed, "title": "Show.S01.1080p.PROPER"}

        by_season, by_episode = service._group_tv_releases(
            [first, duplicate, hashed, hashed_duplicate], [1]
        )

        assert by_episode[(1, 3)] == [first]
        assert by_season[1] == [hashed]


@pytest.mark.asyncio
async def test_staged_overlay_does_not_repaint_completed_episodes(db_session):
    """A staged pack must not turn already-available seasons into "staged".

    A staged season-pack/multi-season/complete-series torrent covers every
    episode in its scope, which previously repainted finished seasons as
    "staged" in the details modal even though the episodes were on Plex.
    """
    request = Request(
        external_id="tv-completed-staged-overlay",
        media_type=MediaType.TV,
        title="Show",
        status=RequestStatus.COMPLETED,
    )
    db_session.add(request)
    await db_session.flush()
    done_season = Season(request_id=request.id, season_number=1, status=RequestStatus.COMPLETED)
    open_season = Season(request_id=request.id, season_number=2, status=RequestStatus.PENDING)
    db_session.add_all([done_season, open_season])
    await db_session.flush()
    db_session.add_all(
        [
            Episode(season_id=done_season.id, episode_number=1, status=RequestStatus.COMPLETED),
            Episode(season_id=done_season.id, episode_number=2, status=RequestStatus.COMPLETED),
            Episode(season_id=open_season.id, episode_number=1, status=RequestStatus.COMPLETED),
            Episode(season_id=open_season.id, episode_number=2, status=RequestStatus.PENDING),
        ]
    )
    await db_session.commit()

    tv_info = await TVEnrichmentService(db_session).load_tv_info(
        request_id=request.id,
        background_tasks=None,
        releases=[],
        active_staged_torrents=[
            {"status": "staged", "target_scope": {"type": "complete_series"}},
        ],
    )

    finished, partial = tv_info.seasons

    # Fully available season keeps its completed badge and stages nothing.
    assert finished["status"] == "completed"
    assert finished["staged_count"] == 0
    finished_episodes = cast("list[dict[str, object]]", finished["episodes"])
    assert [episode["status"] for episode in finished_episodes] == ["completed", "completed"]

    # The season with outstanding work still shows the staging overlay, but only
    # on the episode that is actually missing.
    assert partial["status"] == "staged"
    assert partial["staged_count"] == 1
    partial_episodes = cast("list[dict[str, object]]", partial["episodes"])
    assert [episode["status"] for episode in partial_episodes] == ["completed", "staged"]
