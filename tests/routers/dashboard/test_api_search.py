"""Tests for dashboard search API routes."""

import json
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.siftarr.models.request import MediaType
from app.siftarr.routers import dashboard_api
from app.siftarr.services import dashboard_service
from app.siftarr.services.prowlarr_service import ProwlarrRelease, ProwlarrSearchResult


@pytest.mark.asyncio
async def test_search_all_season_packs_returns_coverage_metadata(mock_db, monkeypatch):
    """Search-all endpoint should surface season coverage for broad TV packs."""
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
    prowlarr_service.search_by_tvdbid.return_value = ProwlarrSearchResult(
        releases=[
            broad_pack,
            compact_broad_pack,
            bare_complete,
            complete_single_season,
            single_episode,
        ],
        query_time_ms=5,
    )
    monkeypatch.setattr(dashboard_service, "ProwlarrService", lambda settings: prowlarr_service)
    monkeypatch.setattr(dashboard_api, "get_settings", lambda: MagicMock())

    fake_evaluation = MagicMock(total_score=12.5, passed=True)
    fake_engine = MagicMock(evaluate=MagicMock(return_value=fake_evaluation))
    monkeypatch.setattr(
        dashboard_service.RuleEngine,
        "from_db_rules",
        MagicMock(return_value=fake_engine),
    )

    response = await dashboard_api.search_all_season_packs(request_id=12, db=mock_db)

    body = json.loads(cast(bytes, response.body))
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
    assert body["releases"][0]["size_per_season_passed"] is True
    assert body["releases"][1]["covered_seasons"] == [1, 2, 3]
    assert body["releases"][1]["covered_season_count"] == 3
    assert body["releases"][1]["covers_all_known_seasons"] is True
    assert body["releases"][1]["is_complete_series"] is False
    assert body["releases"][1]["size_per_season"] == "10.00 GB"
    assert body["releases"][1]["size_per_season_passed"] is True
    assert body["releases"][2]["covered_seasons"] == []
    assert body["releases"][2]["is_complete_series"] is True
    assert body["releases"][2]["size_per_season"] == "14.00 GB"
    assert body["releases"][2]["size_per_season_passed"] is True
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
    rules_result = MagicMock()
    rules_result.scalars.return_value.all.return_value = []
    mock_db.execute.side_effect = [request_result, rules_result]

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
    prowlarr_service.search_by_tvdbid.return_value = ProwlarrSearchResult(
        releases=[
            exact_season,
            multi_season,
            complete_series,
            complete_single_season,
            single_episode,
        ],
        query_time_ms=5,
    )
    monkeypatch.setattr(dashboard_service, "ProwlarrService", lambda settings: prowlarr_service)
    monkeypatch.setattr(dashboard_api, "get_settings", lambda: MagicMock())

    fake_evaluation = MagicMock(total_score=12.5, passed=True)
    fake_engine = MagicMock(evaluate=MagicMock(return_value=fake_evaluation))
    monkeypatch.setattr(
        dashboard_service.RuleEngine,
        "from_db_rules",
        MagicMock(return_value=fake_engine),
    )

    response = await dashboard_api.search_season_packs(request_id=12, season_number=1, db=mock_db)

    body = json.loads(cast(bytes, response.body))
    assert [release["title"] for release in body["releases"]] == [
        "Foundation.Complete.S01.1080p.BluRay",
        "Foundation.S01.2160p.WEB-DL",
    ]


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
    rules_result = MagicMock()
    rules_result.scalars.return_value.all.return_value = []
    mock_db.execute.side_effect = [request_result, rules_result]

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
    prowlarr_service.search_by_tvdbid.return_value = ProwlarrSearchResult(
        releases=[larger_high_score, lower_score, smaller_high_score],
        query_time_ms=5,
    )
    monkeypatch.setattr(dashboard_service, "ProwlarrService", lambda settings: prowlarr_service)
    monkeypatch.setattr(dashboard_api, "get_settings", lambda: MagicMock())

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
        dashboard_service.RuleEngine,
        "from_db_rules",
        MagicMock(return_value=fake_engine),
    )

    response = await dashboard_api.search_season_packs(request_id=12, season_number=1, db=mock_db)

    body = json.loads(cast(bytes, response.body))
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
    rules_result = MagicMock()
    rules_result.scalars.return_value.all.return_value = []
    mock_db.execute.side_effect = [request_result, rules_result]

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
    prowlarr_service.search_by_tvdbid.return_value = ProwlarrSearchResult(
        releases=[size_limit_fail, passing_size_but_other_rule_fail],
        query_time_ms=5,
    )
    monkeypatch.setattr(dashboard_service, "ProwlarrService", lambda settings: prowlarr_service)
    monkeypatch.setattr(dashboard_api, "get_settings", lambda: MagicMock())

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
        dashboard_service.RuleEngine,
        "from_db_rules",
        MagicMock(return_value=fake_engine),
    )

    response = await dashboard_api.search_season_packs(request_id=12, season_number=1, db=mock_db)

    body = json.loads(cast(bytes, response.body))
    assert [release["title"] for release in body["releases"]] == [
        "Foundation.S01.1080p.WEB-DL.BADTAG",
        "Foundation.S01.2160p.REMUX",
    ]
    assert body["releases"][0]["passed"] is False
    assert body["releases"][0]["size_per_season_passed"] is True
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
    season_pack = ProwlarrRelease(
        title="Foundation.S01.2160p.WEB-DL",
        size=30 * 1024 * 1024 * 1024,
        indexer="IndexerB",
        download_url="https://example.test/season-1",
        seeders=77,
        leechers=2,
    )
    multi_season = ProwlarrRelease(
        title="Foundation.S01-S03.2160p.WEB-DL",
        size=42 * 1024 * 1024 * 1024,
        indexer="IndexerC",
        download_url="https://example.test/seasons-1-3",
        seeders=88,
        leechers=1,
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
    complete_single_season = ProwlarrRelease(
        title="Foundation.Complete.S01.1080p.BluRay",
        size=15 * 1024 * 1024 * 1024,
        indexer="IndexerSeason",
        download_url="https://example.test/complete-s01",
        seeders=18,
        leechers=2,
    )
    complete_series = ProwlarrRelease(
        title="Foundation.Complete.Series.1080p.BluRay",
        size=55 * 1024 * 1024 * 1024,
        indexer="IndexerG",
        download_url="https://example.test/complete-series",
        seeders=66,
        leechers=3,
    )

    prowlarr_service = AsyncMock()
    prowlarr_service.search_by_tvdbid.return_value = ProwlarrSearchResult(
        releases=[
            exact_episode,
            season_pack,
            multi_season,
            wrong_episode,
            grouped_episode_compact,
            grouped_episode_ranged,
            complete_single_season,
            complete_series,
        ],
        query_time_ms=5,
    )
    monkeypatch.setattr(dashboard_service, "ProwlarrService", lambda settings: prowlarr_service)
    monkeypatch.setattr(dashboard_api, "get_settings", lambda: MagicMock())

    fake_evaluation = MagicMock(total_score=12.5, passed=True)
    fake_engine = MagicMock(evaluate=MagicMock(return_value=fake_evaluation))
    monkeypatch.setattr(
        dashboard_service.RuleEngine,
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
    assert [release["title"] for release in body["releases"]] == ["Foundation.S01E01.1080p.WEB-DL"]


@pytest.mark.asyncio
async def test_search_all_season_packs_orders_by_score_then_size(mock_db, monkeypatch):
    """Broad season-pack search should prefer higher score, then smaller size."""
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
    prowlarr_service.search_by_tvdbid.return_value = ProwlarrSearchResult(
        releases=[larger_high_score, lower_score, smaller_high_score],
        query_time_ms=5,
    )
    monkeypatch.setattr(dashboard_service, "ProwlarrService", lambda settings: prowlarr_service)
    monkeypatch.setattr(dashboard_api, "get_settings", lambda: MagicMock())

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
        dashboard_service.RuleEngine,
        "from_db_rules",
        MagicMock(return_value=fake_engine),
    )

    response = await dashboard_api.search_all_season_packs(request_id=12, db=mock_db)

    body = json.loads(cast(bytes, response.body))
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
    prowlarr_service.search_by_tvdbid.return_value = ProwlarrSearchResult(
        releases=[larger_high_score, lower_score, smaller_high_score],
        query_time_ms=5,
    )
    monkeypatch.setattr(dashboard_service, "ProwlarrService", lambda settings: prowlarr_service)
    monkeypatch.setattr(dashboard_api, "get_settings", lambda: MagicMock())

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
        dashboard_service.RuleEngine,
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
    assert [release["title"] for release in body["releases"]] == [
        "Foundation.S01E01.1080p.WEB-DL",
        "Foundation.S01E01.2160p.WEB-DL",
        "Foundation.S01E01.HDTV",
    ]
    assert all("_size_bytes" not in release for release in body["releases"])


@pytest.mark.asyncio
async def test_search_all_season_packs_rejects_non_tv_requests(mock_db):
    """Search-all endpoint should reject non-TV requests."""
    request_record = MagicMock()
    request_record.media_type = MediaType.MOVIE

    request_result = MagicMock()
    request_result.scalar_one_or_none.return_value = request_record
    mock_db.execute.return_value = request_result

    with pytest.raises(HTTPException) as exc_info:
        await dashboard_api.search_all_season_packs(request_id=44, db=mock_db)

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Request is not a TV show"
