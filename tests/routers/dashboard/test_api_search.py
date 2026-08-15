"""Tests for dashboard search API routes."""

import json
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.siftarr.models._base import Base
from app.siftarr.models.release import Release
from app.siftarr.models.request import MediaType, RequestStatus
from app.siftarr.models.request import Request as RequestModel
from app.siftarr.models.stats_metrics import StatsTimingEvent
from app.siftarr.routers import dashboard_api
from app.siftarr.services.dashboard import search_service
from app.siftarr.services.dashboard.search_service import SearchService
from app.siftarr.services.integrations.prowlarr_service import ProwlarrRelease, ProwlarrSearchResult


@pytest.mark.asyncio
async def test_process_request_search_persists_search_completed_duration(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    class FakeMovieDecisionService:
        def __init__(self, *args):
            pass

        async def process_request(self, request_id):
            assert request_id is not None
            return {"status": "pending", "message": "searched"}

    monkeypatch.setattr(search_service, "MovieDecisionService", FakeMovieDecisionService)

    async with session_maker() as session:
        request = RequestModel(
            external_id="search-duration-1",
            media_type=MediaType.MOVIE,
            title="Duration Movie",
            status=RequestStatus.PENDING,
        )
        session.add(request)
        await session.commit()

        await SearchService(session).process_request_search(request)
        await session.commit()

        timing = (
            await session.execute(
                select(StatsTimingEvent).where(StatsTimingEvent.event_name == "search_completed")
            )
        ).scalar_one()

    assert timing.duration_ms is not None
    assert timing.duration_ms >= 0
    await engine.dispose()


@pytest.mark.asyncio
async def test_search_multi_season_packs_returns_coverage_metadata(mock_db, monkeypatch):
    """Multi-season endpoint should surface season coverage for broad TV packs."""
    request_record = MagicMock()
    request_record.id = 12
    request_record.media_type = MediaType.TV
    request_record.tvdb_id = 999
    request_record.title = "Foundation"
    request_record.year = 2023

    season_one = MagicMock()
    season_one.season_number = 1
    season_two = MagicMock()
    season_two.season_number = 2
    season_three = MagicMock()
    season_three.season_number = 3

    request_result = MagicMock()
    request_result.scalar_one_or_none.return_value = request_record
    seasons_result = MagicMock()
    seasons_result.scalars.return_value.all.return_value = [season_one, season_two, season_three]
    rules_result = MagicMock()
    rules_result.scalars.return_value.all.return_value = []
    mock_db.execute.side_effect = [request_result, seasons_result, rules_result]

    broad_pack = ProwlarrRelease(
        title="Foundation.S01-S03.2160p.WEB-DL",
        size=30 * 1024 * 1024 * 1024,
        indexer="IndexerA",
        download_url="https://example.test/broad-pack",
        seeders=55,
        leechers=4,
    )
    compact_broad_pack = ProwlarrRelease(
        title="Foundation.S01-03.1080p.WEB-DL",
        size=28 * 1024 * 1024 * 1024,
        indexer="IndexerCompact",
        download_url="https://example.test/compact-broad-pack",
        seeders=44,
        leechers=5,
    )
    bare_complete = ProwlarrRelease(
        title="Foundation.Complete.1080p.BluRay",
        size=42 * 1024 * 1024 * 1024,
        indexer="IndexerB",
        download_url="https://example.test/bare-complete",
        seeders=77,
        leechers=2,
    )
    complete_single_season = ProwlarrRelease(
        title="Foundation.Complete.S01.1080p.BluRay",
        size=14 * 1024 * 1024 * 1024,
        indexer="IndexerSeason",
        download_url="https://example.test/complete-s01",
        seeders=31,
        leechers=2,
    )
    single_episode = ProwlarrRelease(
        title="Foundation.S02E01.1080p.WEB-DL",
        size=2 * 1024 * 1024 * 1024,
        indexer="IndexerC",
        download_url="https://example.test/single-episode",
        seeders=9,
        leechers=1,
    )

    prowlarr_service = AsyncMock()
    prowlarr_service.search_tv_season_sweep.return_value = ProwlarrSearchResult(
        releases=[
            broad_pack,
            compact_broad_pack,
            bare_complete,
            complete_single_season,
            single_episode,
        ],
        query_time_ms=5,
    )
    monkeypatch.setattr(search_service, "ProwlarrService", lambda settings: prowlarr_service)

    fake_evaluation = MagicMock(total_score=12.5, passed=True)
    fake_engine = MagicMock(evaluate=MagicMock(return_value=fake_evaluation))
    monkeypatch.setattr(
        search_service.RuleEngine,
        "from_db_rules",
        MagicMock(return_value=fake_engine),
    )

    response = await dashboard_api.search_multi_season_packs(request_id=12, db=mock_db)

    body = json.loads(cast(bytes, response.body))
    assert body["scope"] == {"type": "multi_season_packs"}
    assert body["known_total_seasons"] == 3
    assert [release["title"] for release in body["releases"]] == [
        "Foundation.S01-03.1080p.WEB-DL",
        "Foundation.S01-S03.2160p.WEB-DL",
        "Foundation.Complete.1080p.BluRay",
    ]
    assert body["releases"][0]["covered_seasons"] == [1, 2, 3]
    assert body["releases"][0]["covered_season_count"] == 3
    assert body["releases"][0]["covers_all_known_seasons"] is True
    assert body["releases"][0]["is_complete_series"] is False
    assert body["releases"][0]["size_per_season"] == "9.33 GB"
    assert body["releases"][0]["size_per_season_bytes"] == round((28 * 1024 * 1024 * 1024) / 3)
    assert body["releases"][0]["size_per_season_passed"] is None
    assert body["releases"][1]["covered_seasons"] == [1, 2, 3]
    assert body["releases"][1]["covered_season_count"] == 3
    assert body["releases"][1]["covers_all_known_seasons"] is True
    assert body["releases"][1]["is_complete_series"] is False
    assert body["releases"][1]["size_per_season"] == "10.00 GB"
    assert body["releases"][1]["size_per_season_passed"] is None
    assert body["releases"][2]["covered_seasons"] == []
    assert body["releases"][2]["is_complete_series"] is True
    assert body["releases"][2]["size_per_season"] == "14.00 GB"
    assert body["releases"][2]["size_per_season_passed"] is None
    assert "Foundation.Complete.S01.1080p.BluRay" not in [
        release["title"] for release in body["releases"]
    ]
    assert body["releases"][0]["status"] == "passed"
    assert body["releases"][0]["status_label"] == "Passed"
    assert body["releases"][0]["stored_release_id"] is None
    assert body["releases"][0]["rejection_reason"] is None
    assert body["releases"][0]["publish_date"] is None


@pytest.mark.asyncio
async def test_search_season_packs_excludes_multi_season_results(mock_db, monkeypatch):
    """Season search should only keep exact single-season packs."""
    request_record = MagicMock()
    request_record.id = 12
    request_record.media_type = MediaType.TV
    request_record.tvdb_id = 999
    request_record.title = "Foundation"
    request_record.year = 2023

    request_result = MagicMock()
    request_result.scalar_one_or_none.return_value = request_record
    episodes_result = MagicMock()
    episodes_result.scalars.return_value.all.return_value = []
    rules_result = MagicMock()
    rules_result.scalars.return_value.all.return_value = []
    mock_db.execute.side_effect = [request_result, episodes_result, rules_result]

    exact_season = ProwlarrRelease(
        title="Foundation.S01.2160p.WEB-DL",
        size=30 * 1024 * 1024 * 1024,
        indexer="IndexerA",
        download_url="https://example.test/season-1",
        seeders=55,
        leechers=4,
    )
    multi_season = ProwlarrRelease(
        title="Foundation.S01-S03.2160p.WEB-DL",
        size=42 * 1024 * 1024 * 1024,
        indexer="IndexerB",
        download_url="https://example.test/seasons-1-3",
        seeders=77,
        leechers=2,
    )
    complete_series = ProwlarrRelease(
        title="Foundation.Complete.Series.1080p.BluRay",
        size=55 * 1024 * 1024 * 1024,
        indexer="IndexerC",
        download_url="https://example.test/complete-series",
        seeders=88,
        leechers=1,
    )
    complete_single_season = ProwlarrRelease(
        title="Foundation.Complete.S01.1080p.BluRay",
        size=28 * 1024 * 1024 * 1024,
        indexer="IndexerSeason",
        download_url="https://example.test/complete-s01",
        seeders=64,
        leechers=2,
    )
    single_episode = ProwlarrRelease(
        title="Foundation.S01E01.1080p.WEB-DL",
        size=2 * 1024 * 1024 * 1024,
        indexer="IndexerD",
        download_url="https://example.test/s01e01",
        seeders=9,
        leechers=1,
    )

    prowlarr_service = AsyncMock()
    prowlarr_service.search_tv_season_sweep.return_value = ProwlarrSearchResult(
        releases=[
            exact_season,
            multi_season,
            complete_series,
            complete_single_season,
            single_episode,
        ],
        query_time_ms=5,
    )
    monkeypatch.setattr(search_service, "ProwlarrService", lambda settings: prowlarr_service)

    fake_evaluation = MagicMock(total_score=12.5, passed=True)
    fake_engine = MagicMock(evaluate=MagicMock(return_value=fake_evaluation))
    monkeypatch.setattr(
        search_service.RuleEngine,
        "from_db_rules",
        MagicMock(return_value=fake_engine),
    )

    response = await dashboard_api.search_season_packs(request_id=12, season_number=1, db=mock_db)

    body = json.loads(cast(bytes, response.body))
    assert body["scope"] == {"type": "season_packs", "season_number": 1}
    assert [release["title"] for release in body["releases"]] == [
        "Foundation.Complete.S01.1080p.BluRay",
        "Foundation.S01.2160p.WEB-DL",
    ]


@pytest.mark.asyncio
async def test_search_season_packs_skips_partly_available_season(mock_db, monkeypatch):
    request_record = MagicMock(
        id=12,
        media_type=MediaType.TV,
        tvdb_id=999,
        title="Foundation",
        year=2023,
    )
    request_result = MagicMock()
    request_result.scalar_one_or_none.return_value = request_record
    episodes_result = MagicMock()
    episodes_result.scalars.return_value.all.return_value = [
        MagicMock(status=RequestStatus.COMPLETED, air_date=None)
    ]
    mock_db.execute.side_effect = [request_result, episodes_result]
    prowlarr_service = AsyncMock()
    monkeypatch.setattr(search_service, "ProwlarrService", lambda settings: prowlarr_service)

    response = await dashboard_api.search_season_packs(request_id=12, season_number=1, db=mock_db)

    body = json.loads(cast(bytes, response.body))
    assert body["releases"] == []
    assert "skipped" in body["error"]
    prowlarr_service.search_tv_season_sweep.assert_not_awaited()


@pytest.mark.asyncio
async def test_search_season_packs_orders_by_score_then_size(mock_db, monkeypatch):
    """Season search results should prefer higher score, then smaller size."""
    request_record = MagicMock()
    request_record.id = 12
    request_record.media_type = MediaType.TV
    request_record.tvdb_id = 999
    request_record.title = "Foundation"
    request_record.year = 2023

    request_result = MagicMock()
    request_result.scalar_one_or_none.return_value = request_record
    episodes_result = MagicMock()
    episodes_result.scalars.return_value.all.return_value = []
    rules_result = MagicMock()
    rules_result.scalars.return_value.all.return_value = []
    mock_db.execute.side_effect = [request_result, episodes_result, rules_result]

    larger_high_score = ProwlarrRelease(
        title="Foundation.S01.2160p.WEB-DL",
        size=30 * 1024 * 1024 * 1024,
        indexer="IndexerA",
        download_url="https://example.test/season-large",
        seeders=55,
        leechers=4,
    )
    smaller_high_score = ProwlarrRelease(
        title="Foundation.Complete.S01.1080p.BluRay",
        size=14 * 1024 * 1024 * 1024,
        indexer="IndexerB",
        download_url="https://example.test/season-small",
        seeders=22,
        leechers=2,
    )
    lower_score = ProwlarrRelease(
        title="Foundation.S01.REMUX",
        size=10 * 1024 * 1024 * 1024,
        indexer="IndexerC",
        download_url="https://example.test/season-low-score",
        seeders=99,
        leechers=1,
    )

    prowlarr_service = AsyncMock()
    prowlarr_service.search_tv_season_sweep.return_value = ProwlarrSearchResult(
        releases=[larger_high_score, lower_score, smaller_high_score],
        query_time_ms=5,
    )
    monkeypatch.setattr(search_service, "ProwlarrService", lambda settings: prowlarr_service)

    score_by_title = {
        larger_high_score.title: 100,
        smaller_high_score.title: 100,
        lower_score.title: 90,
    }
    fake_engine = MagicMock(
        evaluate=MagicMock(
            side_effect=lambda release: MagicMock(
                total_score=score_by_title[release.title], passed=True
            )
        )
    )
    monkeypatch.setattr(
        search_service.RuleEngine,
        "from_db_rules",
        MagicMock(return_value=fake_engine),
    )

    response = await dashboard_api.search_season_packs(request_id=12, season_number=1, db=mock_db)

    body = json.loads(cast(bytes, response.body))
    assert body["scope"] == {"type": "season_packs", "season_number": 1}
    assert [release["title"] for release in body["releases"]] == [
        "Foundation.Complete.S01.1080p.BluRay",
        "Foundation.S01.2160p.WEB-DL",
        "Foundation.S01.REMUX",
    ]
    assert all("_size_bytes" not in release for release in body["releases"])


@pytest.mark.asyncio
async def test_search_season_packs_prioritizes_size_limit_passes(mock_db, monkeypatch):
    """Season search should keep non-size failures green and size failures red."""
    request_record = MagicMock()
    request_record.id = 12
    request_record.media_type = MediaType.TV
    request_record.tvdb_id = 999
    request_record.title = "Foundation"
    request_record.year = 2023

    request_result = MagicMock()
    request_result.scalar_one_or_none.return_value = request_record
    episodes_result = MagicMock()
    episodes_result.scalars.return_value.all.return_value = []
    rules_result = MagicMock()
    rules_result.scalars.return_value.all.return_value = []
    mock_db.execute.side_effect = [request_result, episodes_result, rules_result]

    passing_size_but_other_rule_fail = ProwlarrRelease(
        title="Foundation.S01.1080p.WEB-DL.BADTAG",
        size=14 * 1024 * 1024 * 1024,
        indexer="IndexerA",
        download_url="https://example.test/passing-other-rule",
        seeders=20,
        leechers=2,
    )
    size_limit_fail = ProwlarrRelease(
        title="Foundation.S01.2160p.REMUX",
        size=40 * 1024 * 1024 * 1024,
        indexer="IndexerB",
        download_url="https://example.test/size-fail",
        seeders=99,
        leechers=0,
    )

    prowlarr_service = AsyncMock()
    prowlarr_service.search_tv_season_sweep.return_value = ProwlarrSearchResult(
        releases=[size_limit_fail, passing_size_but_other_rule_fail],
        query_time_ms=5,
    )
    monkeypatch.setattr(search_service, "ProwlarrService", lambda settings: prowlarr_service)

    score_by_title = {
        passing_size_but_other_rule_fail.title: 80,
        size_limit_fail.title: 100,
    }

    def evaluate_release(release):
        if release.title == size_limit_fail.title:
            return MagicMock(
                total_score=score_by_title[release.title],
                passed=False,
                rejection_reason="Size 40.00 GB above maximum 20.00 GB",
            )
        return MagicMock(
            total_score=score_by_title[release.title],
            passed=False,
            rejection_reason="Matched exclusion pattern: Bad Tag",
        )

    fake_engine = MagicMock(evaluate=MagicMock(side_effect=evaluate_release))
    monkeypatch.setattr(
        search_service.RuleEngine,
        "from_db_rules",
        MagicMock(return_value=fake_engine),
    )

    response = await dashboard_api.search_season_packs(request_id=12, season_number=1, db=mock_db)

    body = json.loads(cast(bytes, response.body))
    assert body["scope"] == {"type": "season_packs", "season_number": 1}
    assert [release["title"] for release in body["releases"]] == [
        "Foundation.S01.1080p.WEB-DL.BADTAG",
        "Foundation.S01.2160p.REMUX",
    ]
    assert body["releases"][0]["passed"] is False
    assert body["releases"][0]["size_per_season_passed"] is None
    assert body["releases"][1]["size_per_season_passed"] is False


@pytest.mark.asyncio
async def test_search_episode_excludes_packs_and_multi_season_results(mock_db, monkeypatch):
    """Episode search should only keep exact episode releases."""
    request_record = MagicMock()
    request_record.id = 12
    request_record.media_type = MediaType.TV
    request_record.tvdb_id = 999
    request_record.title = "Foundation"
    request_record.year = 2023

    request_result = MagicMock()
    request_result.scalar_one_or_none.return_value = request_record
    rules_result = MagicMock()
    rules_result.scalars.return_value.all.return_value = []
    mock_db.execute.side_effect = [request_result, rules_result]

    exact_episode = ProwlarrRelease(
        title="Foundation.S01E01.1080p.WEB-DL",
        size=2 * 1024 * 1024 * 1024,
        indexer="IndexerA",
        download_url="https://example.test/s01e01",
        seeders=55,
        leechers=4,
    )
    wrong_episode = ProwlarrRelease(
        title="Foundation.S01E02.1080p.WEB-DL",
        size=2 * 1024 * 1024 * 1024,
        indexer="IndexerD",
        download_url="https://example.test/s01e02",
        seeders=9,
        leechers=1,
    )
    grouped_episode_compact = ProwlarrRelease(
        title="Foundation.S01E01E02.1080p.WEB-DL",
        size=3 * 1024 * 1024 * 1024,
        indexer="IndexerE",
        download_url="https://example.test/s01e01e02",
        seeders=11,
        leechers=2,
    )
    grouped_episode_ranged = ProwlarrRelease(
        title="Foundation.S01E01-E02.1080p.WEB-DL",
        size=3 * 1024 * 1024 * 1024,
        indexer="IndexerF",
        download_url="https://example.test/s01e01-e02",
        seeders=12,
        leechers=2,
    )
    prowlarr_service = AsyncMock()
    prowlarr_service.search_tv_episode_exact.return_value = ProwlarrSearchResult(
        releases=[exact_episode, wrong_episode, grouped_episode_compact, grouped_episode_ranged],
        query_time_ms=5,
    )
    monkeypatch.setattr(search_service, "ProwlarrService", lambda settings: prowlarr_service)

    fake_evaluation = MagicMock(total_score=12.5, passed=True)
    fake_engine = MagicMock(evaluate=MagicMock(return_value=fake_evaluation))
    monkeypatch.setattr(
        search_service.RuleEngine,
        "from_db_rules",
        MagicMock(return_value=fake_engine),
    )

    response = await dashboard_api.search_episode(
        request_id=12,
        season_number=1,
        episode_number=1,
        db=mock_db,
    )

    body = json.loads(cast(bytes, response.body))
    assert body["scope"] == {"type": "single_episode", "season_number": 1, "episode_number": 1}
    assert [release["title"] for release in body["releases"]] == ["Foundation.S01E01.1080p.WEB-DL"]


@pytest.mark.asyncio
async def test_search_episode_falls_back_to_exact_episode_when_sweep_misses_it(
    mock_db, monkeypatch
):
    """Old episodes buried beyond season sweep caps should still be discoverable."""
    request_record = MagicMock()
    request_record.id = 12
    request_record.media_type = MediaType.TV
    request_record.tvdb_id = 999
    request_record.title = "Georgie & Mandy's First Marriage"
    request_record.year = 2024

    request_result = MagicMock()
    request_result.scalar_one_or_none.return_value = request_record
    rules_result = MagicMock()
    rules_result.scalars.return_value.all.return_value = []
    mock_db.execute.side_effect = [request_result, rules_result]

    exact_episode = ProwlarrRelease(
        title="Georgie.And.Mandys.First.Marriage.S02E01.1080p.WEB-DL",
        size=2 * 1024 * 1024 * 1024,
        indexer="IndexerB",
        download_url="https://example.test/s02e01",
        seeders=20,
        leechers=2,
    )

    prowlarr_service = AsyncMock()
    prowlarr_service.search_tv_episode_exact.return_value = ProwlarrSearchResult(
        releases=[exact_episode], query_time_ms=5
    )
    monkeypatch.setattr(search_service, "ProwlarrService", lambda settings: prowlarr_service)

    fake_evaluation = MagicMock(total_score=12.5, passed=True)
    fake_engine = MagicMock(evaluate=MagicMock(return_value=fake_evaluation))
    monkeypatch.setattr(
        search_service.RuleEngine,
        "from_db_rules",
        MagicMock(return_value=fake_engine),
    )

    response = await dashboard_api.search_episode(
        request_id=12,
        season_number=2,
        episode_number=1,
        db=mock_db,
    )

    body = json.loads(cast(bytes, response.body))
    assert body["scope"] == {"type": "single_episode", "season_number": 2, "episode_number": 1}
    assert [release["title"] for release in body["releases"]] == [exact_episode.title]
    prowlarr_service.search_tv_season_sweep.assert_not_awaited()
    prowlarr_service.search_tv_episode_exact.assert_awaited_once_with(
        title="Georgie & Mandy's First Marriage",
        season=2,
        episode=1,
        cacheable=False,
        request_id=12,
    )


@pytest.mark.asyncio
async def test_search_episode_refreshes_exact_results_after_cached_sweep(monkeypatch):
    """A manual episode search must refresh exact results even with a cached match."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with session_maker() as session:
        request = RequestModel(
            external_id="tv-georgie-1",
            media_type=MediaType.TV,
            title="Georgie & Mandy's First Marriage",
            tvdb_id=12345,
            year=2024,
            status=RequestStatus.PENDING,
        )
        session.add(request)
        await session.commit()

        exact_episode = ProwlarrRelease(
            title="Georgie.And.Mandys.First.Marriage.S02E01.1080p.WEB-DL",
            size=2 * 1024 * 1024 * 1024,
            indexer="IndexerA",
            download_url="https://example.test/s02e01",
            seeders=20,
            leechers=2,
        )
        rejected_episode = ProwlarrRelease(
            title="Georgie.And.Mandys.First.Marriage.S02E02.BADTAG.1080p.WEB-DL",
            size=2 * 1024 * 1024 * 1024,
            indexer="IndexerB",
            download_url="https://example.test/s02e02",
            seeders=1,
            leechers=0,
        )
        prowlarr_service = AsyncMock()
        prowlarr_service.search_tv_season_sweep.return_value = ProwlarrSearchResult(
            releases=[exact_episode, rejected_episode], query_time_ms=5
        )
        monkeypatch.setattr(search_service, "ProwlarrService", lambda settings: prowlarr_service)

        service = SearchService(session)
        await service.search_season_packs(request, season_number=2)
        prowlarr_service.reset_mock()
        refreshed_episode = ProwlarrRelease(
            title="Georgie.And.Mandys.First.Marriage.S02E01.REPACK.1080p.WEB-DL",
            size=2 * 1024 * 1024 * 1024,
            indexer="IndexerC",
            download_url="https://example.test/s02e01-repack",
            seeders=30,
            leechers=1,
        )
        prowlarr_service.search_tv_episode_exact.return_value = ProwlarrSearchResult(
            releases=[refreshed_episode], query_time_ms=5
        )

        result = await service.search_episode(request, season_number=2, episode_number=1)

        assert [release["title"] for release in result.releases] == [refreshed_episode.title]
        assert result.releases[0]["stored_release_id"] is not None
        prowlarr_service.search_tv_season_sweep.assert_not_awaited()
        prowlarr_service.search_tv_episode_exact.assert_awaited_once()
        stored_titles = (
            (await session.execute(select(Release.title).where(Release.request_id == request.id)))
            .scalars()
            .all()
        )
        assert exact_episode.title not in stored_titles
        assert rejected_episode.title in stored_titles
        assert refreshed_episode.title in stored_titles

    await engine.dispose()


@pytest.mark.asyncio
async def test_search_episode_returns_exact_provider_error(mock_db, monkeypatch):
    request_record = MagicMock(
        id=12,
        media_type=MediaType.TV,
        title="Sterling Point",
        tvdb_id=459552,
        year=2026,
    )
    request_result = MagicMock()
    request_result.scalar_one_or_none.return_value = request_record
    mock_db.execute.return_value = request_result

    prowlarr_service = AsyncMock()
    prowlarr_service.search_tv_episode_exact.return_value = ProwlarrSearchResult(
        releases=[], query_time_ms=5, error="HTTP 503"
    )
    monkeypatch.setattr(search_service, "ProwlarrService", lambda settings: prowlarr_service)

    response = await dashboard_api.search_episode(
        request_id=12,
        season_number=1,
        episode_number=1,
        db=mock_db,
    )

    body = json.loads(cast(bytes, response.body))
    assert body["releases"] == []
    assert body["error"] == "HTTP 503"


@pytest.mark.asyncio
async def test_search_episode_exact_fallback_when_cached_sweep_misses_episode(monkeypatch):
    """If cached and refreshed sweeps lack the exact episode, use exact fallback."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with session_maker() as session:
        request = RequestModel(
            external_id="tv-georgie-2",
            media_type=MediaType.TV,
            title="Georgie & Mandy's First Marriage",
            tvdb_id=12345,
            year=2024,
            status=RequestStatus.PENDING,
        )
        session.add(request)
        await session.commit()

        exact_episode = ProwlarrRelease(
            title="Georgie.And.Mandys.First.Marriage.S02E01.1080p.WEB-DL",
            size=2 * 1024 * 1024 * 1024,
            indexer="IndexerB",
            download_url="https://example.test/s02e01",
            seeders=10,
            leechers=1,
        )
        wrong_show = ProwlarrRelease(
            title="Evil.S02E01.Georgie.And.Mandys.First.Marriage.1080p.WEB-DL",
            size=2 * 1024 * 1024 * 1024,
            indexer="IndexerC",
            download_url="https://example.test/wrong-show",
            seeders=99,
            leechers=1,
        )
        prowlarr_service = AsyncMock()
        prowlarr_service.search_tv_episode_exact.return_value = ProwlarrSearchResult(
            releases=[wrong_show, exact_episode], query_time_ms=5
        )
        monkeypatch.setattr(search_service, "ProwlarrService", lambda settings: prowlarr_service)

        service = SearchService(session)
        await service.search_season_packs(request, season_number=2)
        prowlarr_service.reset_mock()

        result = await service.search_episode(request, season_number=2, episode_number=1)

        assert [release["title"] for release in result.releases] == [exact_episode.title]
        prowlarr_service.search_tv_season_sweep.assert_not_awaited()
        prowlarr_service.search_tv_episode_exact.assert_awaited_once()
        stored_titles = (await session.execute(select(Release.title))).scalars().all()
        assert wrong_show.title not in stored_titles

    await engine.dispose()


@pytest.mark.asyncio
async def test_search_multi_season_packs_orders_by_score_then_size(mock_db, monkeypatch):
    """Multi-season search should prefer higher score, then smaller size."""
    request_record = MagicMock()
    request_record.id = 12
    request_record.media_type = MediaType.TV
    request_record.tvdb_id = 999
    request_record.title = "Foundation"
    request_record.year = 2023

    season_one = MagicMock()
    season_one.season_number = 1
    season_two = MagicMock()
    season_two.season_number = 2

    request_result = MagicMock()
    request_result.scalar_one_or_none.return_value = request_record
    seasons_result = MagicMock()
    seasons_result.scalars.return_value.all.return_value = [season_one, season_two]
    rules_result = MagicMock()
    rules_result.scalars.return_value.all.return_value = []
    mock_db.execute.side_effect = [request_result, seasons_result, rules_result]

    larger_high_score = ProwlarrRelease(
        title="Foundation.S01-S02.2160p.WEB-DL",
        size=30 * 1024 * 1024 * 1024,
        indexer="IndexerA",
        download_url="https://example.test/broad-large",
        seeders=55,
        leechers=4,
    )
    smaller_high_score = ProwlarrRelease(
        title="Foundation.S01-02.1080p.WEB-DL",
        size=20 * 1024 * 1024 * 1024,
        indexer="IndexerB",
        download_url="https://example.test/broad-small",
        seeders=20,
        leechers=2,
    )
    lower_score = ProwlarrRelease(
        title="Foundation.Complete.720p.WEB-DL",
        size=10 * 1024 * 1024 * 1024,
        indexer="IndexerC",
        download_url="https://example.test/broad-low-score",
        seeders=99,
        leechers=1,
    )

    prowlarr_service = AsyncMock()
    prowlarr_service.search_tv_season_sweep.return_value = ProwlarrSearchResult(
        releases=[larger_high_score, lower_score, smaller_high_score],
        query_time_ms=5,
    )
    monkeypatch.setattr(search_service, "ProwlarrService", lambda settings: prowlarr_service)

    score_by_title = {
        larger_high_score.title: 100,
        smaller_high_score.title: 100,
        lower_score.title: 90,
    }
    fake_engine = MagicMock(
        evaluate=MagicMock(
            side_effect=lambda release: MagicMock(
                total_score=score_by_title[release.title], passed=True
            )
        )
    )
    monkeypatch.setattr(
        search_service.RuleEngine,
        "from_db_rules",
        MagicMock(return_value=fake_engine),
    )

    response = await dashboard_api.search_multi_season_packs(request_id=12, db=mock_db)

    body = json.loads(cast(bytes, response.body))
    assert body["scope"] == {"type": "multi_season_packs"}
    assert [release["title"] for release in body["releases"]] == [
        "Foundation.S01-02.1080p.WEB-DL",
        "Foundation.S01-S02.2160p.WEB-DL",
        "Foundation.Complete.720p.WEB-DL",
    ]
    assert all("_size_bytes" not in release for release in body["releases"])


@pytest.mark.asyncio
async def test_search_episode_orders_by_score_then_size(mock_db, monkeypatch):
    """Episode search results should prefer higher score, then smaller size."""
    request_record = MagicMock()
    request_record.id = 12
    request_record.media_type = MediaType.TV
    request_record.tvdb_id = 999
    request_record.title = "Foundation"
    request_record.year = 2023

    request_result = MagicMock()
    request_result.scalar_one_or_none.return_value = request_record
    rules_result = MagicMock()
    rules_result.scalars.return_value.all.return_value = []
    mock_db.execute.side_effect = [request_result, rules_result]

    larger_high_score = ProwlarrRelease(
        title="Foundation.S01E01.2160p.WEB-DL",
        size=5 * 1024 * 1024 * 1024,
        indexer="IndexerA",
        download_url="https://example.test/episode-large",
        seeders=55,
        leechers=4,
    )
    smaller_high_score = ProwlarrRelease(
        title="Foundation.S01E01.1080p.WEB-DL",
        size=2 * 1024 * 1024 * 1024,
        indexer="IndexerB",
        download_url="https://example.test/episode-small",
        seeders=10,
        leechers=2,
    )
    lower_score = ProwlarrRelease(
        title="Foundation.S01E01.HDTV",
        size=1 * 1024 * 1024 * 1024,
        indexer="IndexerC",
        download_url="https://example.test/episode-low-score",
        seeders=99,
        leechers=1,
    )

    prowlarr_service = AsyncMock()
    prowlarr_service.search_tv_episode_exact.return_value = ProwlarrSearchResult(
        releases=[larger_high_score, lower_score, smaller_high_score],
        query_time_ms=5,
    )
    monkeypatch.setattr(search_service, "ProwlarrService", lambda settings: prowlarr_service)

    score_by_title = {
        larger_high_score.title: 100,
        smaller_high_score.title: 100,
        lower_score.title: 90,
    }
    fake_engine = MagicMock(
        evaluate=MagicMock(
            side_effect=lambda release: MagicMock(
                total_score=score_by_title[release.title], passed=True
            )
        )
    )
    monkeypatch.setattr(
        search_service.RuleEngine,
        "from_db_rules",
        MagicMock(return_value=fake_engine),
    )

    response = await dashboard_api.search_episode(
        request_id=12,
        season_number=1,
        episode_number=1,
        db=mock_db,
    )

    body = json.loads(cast(bytes, response.body))
    assert body["scope"] == {"type": "single_episode", "season_number": 1, "episode_number": 1}
    assert [release["title"] for release in body["releases"]] == [
        "Foundation.S01E01.1080p.WEB-DL",
        "Foundation.S01E01.2160p.WEB-DL",
        "Foundation.S01E01.HDTV",
    ]
    assert all("_size_bytes" not in release for release in body["releases"])


@pytest.mark.asyncio
async def test_search_multi_season_packs_includes_broad_pack_query(mock_db, monkeypatch):
    """Per-season sweeps cannot match "S01-S03"/"Complete" titles, so the broad
    title-only pack query must run and its results must reach the response."""
    request_record = MagicMock()
    request_record.id = 12
    request_record.media_type = MediaType.TV
    request_record.tvdb_id = 999
    request_record.title = "Foundation"
    request_record.year = 2023

    season_one = MagicMock(season_number=1)
    season_two = MagicMock(season_number=2)

    request_result = MagicMock()
    request_result.scalar_one_or_none.return_value = request_record
    seasons_result = MagicMock()
    seasons_result.scalars.return_value.all.return_value = [season_one, season_two]
    rules_result = MagicMock()
    rules_result.scalars.return_value.all.return_value = []
    mock_db.execute.side_effect = [request_result, seasons_result, rules_result]

    broad_pack = ProwlarrRelease(
        title="Foundation.S01-S02.1080p.WEB-DL",
        size=20 * 1024 * 1024 * 1024,
        indexer="IndexerBroad",
        download_url="https://example.test/broad-only",
        seeders=60,
        leechers=3,
    )

    prowlarr_service = AsyncMock()
    # Season sweeps find nothing: only the broad query can surface the pack.
    prowlarr_service.search_tv_season_sweep.return_value = ProwlarrSearchResult(
        releases=[], query_time_ms=1
    )
    prowlarr_service.search_tv_packs_broad.return_value = ProwlarrSearchResult(
        releases=[broad_pack], query_time_ms=2
    )
    monkeypatch.setattr(search_service, "ProwlarrService", lambda settings: prowlarr_service)

    fake_evaluation = MagicMock(total_score=10.0, passed=True)
    monkeypatch.setattr(
        search_service.RuleEngine,
        "from_db_rules",
        MagicMock(return_value=MagicMock(evaluate=MagicMock(return_value=fake_evaluation))),
    )

    response = await dashboard_api.search_multi_season_packs(request_id=12, db=mock_db)

    prowlarr_service.search_tv_packs_broad.assert_awaited_once()
    assert prowlarr_service.search_tv_packs_broad.await_args.kwargs["title"] == "Foundation"
    body = json.loads(cast(bytes, response.body))
    assert [release["title"] for release in body["releases"]] == ["Foundation.S01-S02.1080p.WEB-DL"]


@pytest.mark.asyncio
async def test_search_multi_season_packs_survives_broad_query_failure(mock_db, monkeypatch):
    """A failing broad query must not lose the per-season sweep results."""
    request_record = MagicMock()
    request_record.id = 12
    request_record.media_type = MediaType.TV
    request_record.tvdb_id = 999
    request_record.title = "Foundation"
    request_record.year = 2023

    request_result = MagicMock()
    request_result.scalar_one_or_none.return_value = request_record
    seasons_result = MagicMock()
    seasons_result.scalars.return_value.all.return_value = [MagicMock(season_number=1)]
    rules_result = MagicMock()
    rules_result.scalars.return_value.all.return_value = []
    mock_db.execute.side_effect = [request_result, seasons_result, rules_result]

    sweep_pack = ProwlarrRelease(
        title="Foundation.Complete.1080p.BluRay",
        size=40 * 1024 * 1024 * 1024,
        indexer="IndexerA",
        download_url="https://example.test/complete",
        seeders=20,
        leechers=1,
    )

    prowlarr_service = AsyncMock()
    prowlarr_service.search_tv_season_sweep.return_value = ProwlarrSearchResult(
        releases=[sweep_pack], query_time_ms=1
    )
    prowlarr_service.search_tv_packs_broad.side_effect = RuntimeError("indexer down")
    monkeypatch.setattr(search_service, "ProwlarrService", lambda settings: prowlarr_service)

    fake_evaluation = MagicMock(total_score=10.0, passed=True)
    monkeypatch.setattr(
        search_service.RuleEngine,
        "from_db_rules",
        MagicMock(return_value=MagicMock(evaluate=MagicMock(return_value=fake_evaluation))),
    )

    response = await dashboard_api.search_multi_season_packs(request_id=12, db=mock_db)

    body = json.loads(cast(bytes, response.body))
    assert [release["title"] for release in body["releases"]] == [
        "Foundation.Complete.1080p.BluRay"
    ]


@pytest.mark.asyncio
async def test_search_multi_season_packs_rejects_non_tv_requests(mock_db):
    """Multi-season endpoint should reject non-TV requests."""
    request_record = MagicMock()
    request_record.media_type = MediaType.MOVIE

    request_result = MagicMock()
    request_result.scalar_one_or_none.return_value = request_record
    mock_db.execute.return_value = request_result

    with pytest.raises(HTTPException) as exc_info:
        await dashboard_api.search_multi_season_packs(request_id=44, db=mock_db)

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Request is not a TV show"
