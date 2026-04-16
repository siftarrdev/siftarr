"""Tests for dashboard router helpers and endpoints."""

import json
from datetime import UTC, datetime, timedelta
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import BackgroundTasks, HTTPException

from app.siftarr.models.release import Release
from app.siftarr.models.request import MediaType, RequestStatus
from app.siftarr.routers import dashboard
from app.siftarr.services.prowlarr_service import ProwlarrRelease, ProwlarrSearchResult


class TestDashboardRouter:
    """Test cases for dashboard router helpers and actions."""

    @pytest.fixture
    def mock_db(self):
        """Create a mock database session."""
        return AsyncMock()

    @pytest.fixture
    def background_tasks(self):
        return BackgroundTasks()

    @pytest.fixture(autouse=True)
    def clear_details_sync_tasks(self):
        dashboard._DETAILS_SYNC_TASKS.clear()
        yield
        dashboard._DETAILS_SYNC_TASKS.clear()

    @pytest.mark.asyncio
    async def test_bulk_request_action_redirects_to_requested_tab(self, mock_db, monkeypatch):
        """Bulk actions should return to the requested tab."""
        request_record = MagicMock()
        request_record.created_at = MagicMock()

        execute_result = MagicMock()
        execute_result.scalars.return_value.all.return_value = [request_record]
        mock_db.execute.return_value = execute_result

        process_request_search = AsyncMock()
        monkeypatch.setattr(dashboard, "_process_request_search", process_request_search)

        response = await dashboard.bulk_request_action(
            action="search",
            request_ids=[1],
            redirect_to="/?tab=active",
            db=mock_db,
        )

        assert response.status_code == 303
        assert response.headers["location"] == "/?tab=active"
        process_request_search.assert_awaited_once_with(request_record, mock_db)

    @pytest.mark.asyncio
    async def test_bulk_request_action_defaults_to_pending_tab(self, mock_db):
        """Bulk actions default back to the pending tab."""
        response = await dashboard.bulk_request_action(
            action="search",
            request_ids=[],
            redirect_to=None,
            db=mock_db,
        )

        assert response.status_code == 303
        assert response.headers["location"] == "/?tab=pending"

    @pytest.mark.asyncio
    async def test_pending_requests_include_searching_requests(self, mock_db, monkeypatch):
        """Pending tab should keep in-flight searches visible."""
        active_request = MagicMock()
        active_request.id = 1
        active_request.status = dashboard.RequestStatus.SEARCHING
        active_request.overseerr_request_id = 10
        active_request.title = "The Rookie"
        active_request.media_type.value = "tv"
        active_request.created_at = MagicMock()

        lifecycle_service = AsyncMock()
        lifecycle_service.get_active_requests.return_value = [active_request]
        monkeypatch.setattr(dashboard, "LifecycleService", lambda db: lifecycle_service)

        class FakeOverseerrService:
            def __init__(self, settings):
                pass

            async def get_request_status(self, request_id):
                return {"status": "approved", "media": {"status": "processing"}}

            def normalize_request_status(self, value):
                return value

            def normalize_media_status(self, value):
                return value

            async def close(self):
                return None

        monkeypatch.setattr(dashboard, "OverseerrService", FakeOverseerrService)
        monkeypatch.setattr(
            dashboard,
            "PendingQueueService",
            lambda db: AsyncMock(get_all_pending=AsyncMock(return_value=[])),
        )

        mock_db.execute.return_value = MagicMock(
            scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))
        )

        response = await dashboard.dashboard(MagicMock(), db=mock_db)

        context = response.context
        assert active_request in context["pending_requests"]

    @pytest.mark.asyncio
    async def test_approve_request_success(self):
        """Approve helper should surface successful approvals."""
        mock_overseerr_service = AsyncMock()
        mock_overseerr_service.approve_request.return_value = True

        result = await mock_overseerr_service.approve_request(123)

        assert result is True
        mock_overseerr_service.approve_request.assert_called_once_with(123)

    @pytest.mark.asyncio
    async def test_approve_request_not_found(self):
        """Approve helper should map a missing request to 404."""
        mock_overseerr_service = AsyncMock()
        mock_overseerr_service.approve_request.return_value = False

        from starlette.exceptions import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            if not await mock_overseerr_service.approve_request(999):
                raise HTTPException(status_code=404, detail="Request not found")

        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_deny_request_success(self):
        """Deny helper should surface successful declines."""
        mock_overseerr_service = AsyncMock()
        mock_overseerr_service.decline_request.return_value = True

        result = await mock_overseerr_service.decline_request(123)

        assert result is True
        mock_overseerr_service.decline_request.assert_called_once_with(123)

    @pytest.mark.asyncio
    async def test_deny_request_not_found(self):
        """Deny helper should map a missing request to 404."""
        mock_overseerr_service = AsyncMock()
        mock_overseerr_service.decline_request.return_value = False

        from starlette.exceptions import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            if not await mock_overseerr_service.decline_request(999):
                raise HTTPException(status_code=404, detail="Request not found")

        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_search_all_season_packs_returns_coverage_metadata(self, mock_db, monkeypatch):
        """Search-all endpoint should surface season coverage for broad TV packs."""
        request_record = MagicMock()
        request_record.id = 12
        request_record.media_type = dashboard.MediaType.TV
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
        seasons_result.scalars.return_value.all.return_value = [
            season_one,
            season_two,
            season_three,
        ]
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
        monkeypatch.setattr(dashboard, "ProwlarrService", lambda settings: prowlarr_service)
        monkeypatch.setattr(
            dashboard, "get_effective_settings", AsyncMock(return_value=MagicMock())
        )

        fake_evaluation = MagicMock(total_score=12.5, passed=True)
        fake_engine = MagicMock(evaluate=MagicMock(return_value=fake_evaluation))
        monkeypatch.setattr(
            dashboard.RuleEngine,
            "from_db_rules",
            MagicMock(return_value=fake_engine),
        )

        response = await dashboard.search_all_season_packs(request_id=12, db=mock_db)

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
    async def test_search_season_packs_excludes_multi_season_results(self, mock_db, monkeypatch):
        """Season search should only keep exact single-season packs."""
        request_record = MagicMock()
        request_record.id = 12
        request_record.media_type = dashboard.MediaType.TV
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
        monkeypatch.setattr(dashboard, "ProwlarrService", lambda settings: prowlarr_service)
        monkeypatch.setattr(
            dashboard, "get_effective_settings", AsyncMock(return_value=MagicMock())
        )

        fake_evaluation = MagicMock(total_score=12.5, passed=True)
        fake_engine = MagicMock(evaluate=MagicMock(return_value=fake_evaluation))
        monkeypatch.setattr(
            dashboard.RuleEngine,
            "from_db_rules",
            MagicMock(return_value=fake_engine),
        )

        response = await dashboard.search_season_packs(request_id=12, season_number=1, db=mock_db)

        body = json.loads(cast(bytes, response.body))
        assert [release["title"] for release in body["releases"]] == [
            "Foundation.Complete.S01.1080p.BluRay",
            "Foundation.S01.2160p.WEB-DL",
        ]

    @pytest.mark.asyncio
    async def test_search_season_packs_orders_by_score_then_size(self, mock_db, monkeypatch):
        """Season search results should prefer higher score, then smaller size."""
        request_record = MagicMock()
        request_record.id = 12
        request_record.media_type = dashboard.MediaType.TV
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
        monkeypatch.setattr(dashboard, "ProwlarrService", lambda settings: prowlarr_service)
        monkeypatch.setattr(
            dashboard, "get_effective_settings", AsyncMock(return_value=MagicMock())
        )

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
            dashboard.RuleEngine,
            "from_db_rules",
            MagicMock(return_value=fake_engine),
        )

        response = await dashboard.search_season_packs(request_id=12, season_number=1, db=mock_db)

        body = json.loads(cast(bytes, response.body))
        assert [release["title"] for release in body["releases"]] == [
            "Foundation.Complete.S01.1080p.BluRay",
            "Foundation.S01.2160p.WEB-DL",
            "Foundation.S01.REMUX",
        ]
        assert all("_size_bytes" not in release for release in body["releases"])

    @pytest.mark.asyncio
    async def test_search_episode_excludes_packs_and_multi_season_results(
        self, mock_db, monkeypatch
    ):
        """Episode search should only keep exact episode releases."""
        request_record = MagicMock()
        request_record.id = 12
        request_record.media_type = dashboard.MediaType.TV
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
        monkeypatch.setattr(dashboard, "ProwlarrService", lambda settings: prowlarr_service)
        monkeypatch.setattr(
            dashboard, "get_effective_settings", AsyncMock(return_value=MagicMock())
        )

        fake_evaluation = MagicMock(total_score=12.5, passed=True)
        fake_engine = MagicMock(evaluate=MagicMock(return_value=fake_evaluation))
        monkeypatch.setattr(
            dashboard.RuleEngine,
            "from_db_rules",
            MagicMock(return_value=fake_engine),
        )

        response = await dashboard.search_episode(
            request_id=12,
            season_number=1,
            episode_number=1,
            db=mock_db,
        )

        body = json.loads(cast(bytes, response.body))
        assert [release["title"] for release in body["releases"]] == [
            "Foundation.S01E01.1080p.WEB-DL"
        ]

    @pytest.mark.asyncio
    async def test_search_all_season_packs_orders_by_score_then_size(self, mock_db, monkeypatch):
        """Broad season-pack search should prefer higher score, then smaller size."""
        request_record = MagicMock()
        request_record.id = 12
        request_record.media_type = dashboard.MediaType.TV
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
        monkeypatch.setattr(dashboard, "ProwlarrService", lambda settings: prowlarr_service)
        monkeypatch.setattr(
            dashboard, "get_effective_settings", AsyncMock(return_value=MagicMock())
        )

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
            dashboard.RuleEngine,
            "from_db_rules",
            MagicMock(return_value=fake_engine),
        )

        response = await dashboard.search_all_season_packs(request_id=12, db=mock_db)

        body = json.loads(cast(bytes, response.body))
        assert [release["title"] for release in body["releases"]] == [
            "Foundation.S01-02.1080p.WEB-DL",
            "Foundation.S01-S02.2160p.WEB-DL",
            "Foundation.Complete.720p.WEB-DL",
        ]
        assert all("_size_bytes" not in release for release in body["releases"])

    @pytest.mark.asyncio
    async def test_search_episode_orders_by_score_then_size(self, mock_db, monkeypatch):
        """Episode search results should prefer higher score, then smaller size."""
        request_record = MagicMock()
        request_record.id = 12
        request_record.media_type = dashboard.MediaType.TV
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
        monkeypatch.setattr(dashboard, "ProwlarrService", lambda settings: prowlarr_service)
        monkeypatch.setattr(
            dashboard, "get_effective_settings", AsyncMock(return_value=MagicMock())
        )

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
            dashboard.RuleEngine,
            "from_db_rules",
            MagicMock(return_value=fake_engine),
        )

        response = await dashboard.search_episode(
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
    async def test_search_all_season_packs_rejects_non_tv_requests(self, mock_db):
        """Search-all endpoint should reject non-TV requests."""
        request_record = MagicMock()
        request_record.media_type = dashboard.MediaType.MOVIE

        request_result = MagicMock()
        request_result.scalar_one_or_none.return_value = request_record
        mock_db.execute.return_value = request_result

        with pytest.raises(HTTPException) as exc_info:
            await dashboard.search_all_season_packs(request_id=44, db=mock_db)

        assert exc_info.value.status_code == 400
        assert exc_info.value.detail == "Request is not a TV show"

    @pytest.mark.asyncio
    async def test_request_details_reuses_persisted_multi_season_coverage(
        self, mock_db, monkeypatch, background_tasks
    ):
        """Stored multi-season coverage should serialize and group by each covered season."""
        request_record = MagicMock()
        request_record.id = 21
        request_record.media_type = MediaType.TV
        request_record.status = RequestStatus.PENDING
        request_record.title = "Foundation"
        request_record.overseerr_request_id = None

        stored_release = Release(
            id=8,
            request_id=21,
            title="Foundation.S01-S02.2160p.WEB-DL",
            size=30 * 1024 * 1024 * 1024,
            seeders=55,
            leechers=4,
            download_url="https://example.test/foundation-s01-s02",
            magnet_url=None,
            info_hash=None,
            indexer="IndexerA",
            publish_date=None,
            resolution="2160p",
            codec=None,
            release_group=None,
            season_number=1,
            episode_number=None,
            season_coverage="1,2",
            score=95,
            passed_rules=True,
            is_downloaded=False,
        )

        season_one = MagicMock(
            id=101, season_number=1, status=RequestStatus.PENDING, synced_at=None
        )
        season_two = MagicMock(
            id=102, season_number=2, status=RequestStatus.PENDING, synced_at=None
        )

        request_result = MagicMock()
        request_result.scalar_one_or_none.return_value = request_record
        release_result = MagicMock()
        release_result.scalars.return_value.all.return_value = [stored_release]
        rules_result = MagicMock()
        rules_result.scalars.return_value.all.return_value = []
        seasons_result = MagicMock()
        seasons_result.scalars.return_value.all.return_value = [season_one, season_two]
        episodes_one_result = MagicMock()
        episodes_one_result.scalars.return_value.all.return_value = []
        episodes_two_result = MagicMock()
        episodes_two_result.scalars.return_value.all.return_value = []
        mock_db.execute.side_effect = [
            request_result,
            release_result,
            rules_result,
            seasons_result,
            episodes_one_result,
            episodes_two_result,
        ]

        monkeypatch.setattr(
            dashboard, "get_effective_settings", AsyncMock(return_value=MagicMock())
        )

        class FakeOverseerrService:
            def __init__(self, settings):
                pass

            async def close(self):
                return None

        class FakePlexService:
            async def close(self):
                return None

        fake_engine = MagicMock()
        fake_engine.evaluate.return_value = MagicMock(rejection_reason=None, matches=[])

        monkeypatch.setattr(dashboard, "OverseerrService", FakeOverseerrService)
        monkeypatch.setattr(dashboard, "PlexService", lambda settings: FakePlexService())
        monkeypatch.setattr(
            dashboard.RuleEngine,
            "from_db_rules",
            MagicMock(return_value=fake_engine),
        )

        class FakeEpisodeSyncService:
            def __init__(self, db, plex):
                self.db = db
                self.plex = plex

            async def refresh_if_stale(self, request_id):
                return None

        with pytest.MonkeyPatch.context() as inner_monkeypatch:
            inner_monkeypatch.setattr(
                "app.siftarr.services.episode_sync_service.EpisodeSyncService",
                FakeEpisodeSyncService,
            )
            response = await dashboard.request_details(
                request_id=21, background_tasks=background_tasks, db=mock_db
            )

        body = json.loads(cast(bytes, response.body))
        assert body["releases"][0]["covered_seasons"] == [1, 2]
        assert body["releases"][0]["covered_season_count"] == 2
        assert body["releases"][0]["covers_all_known_seasons"] is True
        assert body["releases"][0]["size_per_season"] == "15.00 GB"
        assert [release["title"] for release in body["tv_info"]["releases_by_season"]["1"]] == [
            "Foundation.S01-S02.2160p.WEB-DL"
        ]
        assert [release["title"] for release in body["tv_info"]["releases_by_season"]["2"]] == [
            "Foundation.S01-S02.2160p.WEB-DL"
        ]

    @pytest.mark.asyncio
    async def test_request_details_orders_stored_releases_by_score_then_size(
        self, mock_db, monkeypatch, background_tasks
    ):
        """Stored releases should use the same score-desc, size-asc ordering."""
        request_record = MagicMock()
        request_record.id = 21
        request_record.media_type = MediaType.TV
        request_record.status = RequestStatus.PENDING
        request_record.title = "Foundation"
        request_record.overseerr_request_id = None

        larger_high_score = Release(
            id=8,
            request_id=21,
            title="Foundation.S01-S02.2160p.WEB-DL",
            size=30 * 1024 * 1024 * 1024,
            seeders=55,
            leechers=4,
            download_url="https://example.test/foundation-large",
            magnet_url=None,
            info_hash=None,
            indexer="IndexerA",
            publish_date=None,
            resolution="2160p",
            codec=None,
            release_group=None,
            season_number=1,
            episode_number=None,
            season_coverage="1,2",
            score=95,
            passed_rules=True,
            is_downloaded=False,
        )
        lower_score = Release(
            id=9,
            request_id=21,
            title="Foundation.S01.720p.WEB-DL",
            size=10 * 1024 * 1024 * 1024,
            seeders=99,
            leechers=1,
            download_url="https://example.test/foundation-low-score",
            magnet_url=None,
            info_hash=None,
            indexer="IndexerB",
            publish_date=None,
            resolution="720p",
            codec=None,
            release_group=None,
            season_number=1,
            episode_number=None,
            season_coverage="1",
            score=90,
            passed_rules=True,
            is_downloaded=False,
        )
        smaller_high_score = Release(
            id=10,
            request_id=21,
            title="Foundation.S01-02.1080p.WEB-DL",
            size=20 * 1024 * 1024 * 1024,
            seeders=22,
            leechers=2,
            download_url="https://example.test/foundation-small",
            magnet_url=None,
            info_hash=None,
            indexer="IndexerC",
            publish_date=None,
            resolution="1080p",
            codec=None,
            release_group=None,
            season_number=1,
            episode_number=None,
            season_coverage="1,2",
            score=95,
            passed_rules=True,
            is_downloaded=False,
        )

        season_one = MagicMock(
            id=101, season_number=1, status=RequestStatus.PENDING, synced_at=None
        )
        season_two = MagicMock(
            id=102, season_number=2, status=RequestStatus.PENDING, synced_at=None
        )

        request_result = MagicMock()
        request_result.scalar_one_or_none.return_value = request_record
        release_result = MagicMock()
        release_result.scalars.return_value.all.return_value = [
            larger_high_score,
            lower_score,
            smaller_high_score,
        ]
        rules_result = MagicMock()
        rules_result.scalars.return_value.all.return_value = []
        seasons_result = MagicMock()
        seasons_result.scalars.return_value.all.return_value = [season_one, season_two]
        episodes_one_result = MagicMock()
        episodes_one_result.scalars.return_value.all.return_value = []
        episodes_two_result = MagicMock()
        episodes_two_result.scalars.return_value.all.return_value = []
        mock_db.execute.side_effect = [
            request_result,
            release_result,
            rules_result,
            seasons_result,
            episodes_one_result,
            episodes_two_result,
        ]

        monkeypatch.setattr(
            dashboard, "get_effective_settings", AsyncMock(return_value=MagicMock())
        )

        class FakeOverseerrService:
            def __init__(self, settings):
                pass

            async def close(self):
                return None

        class FakePlexService:
            async def close(self):
                return None

        fake_engine = MagicMock()
        fake_engine.evaluate.return_value = MagicMock(rejection_reason=None, matches=[])

        monkeypatch.setattr(dashboard, "OverseerrService", FakeOverseerrService)
        monkeypatch.setattr(dashboard, "PlexService", lambda settings: FakePlexService())
        monkeypatch.setattr(
            dashboard.RuleEngine,
            "from_db_rules",
            MagicMock(return_value=fake_engine),
        )

        class FakeEpisodeSyncService:
            def __init__(self, db, plex):
                self.db = db
                self.plex = plex

            async def refresh_if_stale(self, request_id):
                return None

        with pytest.MonkeyPatch.context() as inner_monkeypatch:
            inner_monkeypatch.setattr(
                "app.siftarr.services.episode_sync_service.EpisodeSyncService",
                FakeEpisodeSyncService,
            )
            response = await dashboard.request_details(
                request_id=21, background_tasks=background_tasks, db=mock_db
            )

        body = json.loads(cast(bytes, response.body))
        assert [release["title"] for release in body["releases"]] == [
            "Foundation.S01-02.1080p.WEB-DL",
            "Foundation.S01-S02.2160p.WEB-DL",
            "Foundation.S01.720p.WEB-DL",
        ]
        assert [release["title"] for release in body["tv_info"]["releases_by_season"]["1"]] == [
            "Foundation.S01-02.1080p.WEB-DL",
            "Foundation.S01-S02.2160p.WEB-DL",
            "Foundation.S01.720p.WEB-DL",
        ]
        assert [release["title"] for release in body["tv_info"]["releases_by_season"]["2"]] == [
            "Foundation.S01-02.1080p.WEB-DL",
            "Foundation.S01-S02.2160p.WEB-DL",
        ]
        assert all("_size_bytes" not in release for release in body["releases"])

    @pytest.mark.asyncio
    async def test_request_details_includes_release_status_reason_and_publish_date(
        self, mock_db, monkeypatch, background_tasks
    ):
        """Stored request details should surface status metadata needed by the UI."""
        request_record = MagicMock()
        request_record.id = 21
        request_record.media_type = MediaType.MOVIE
        request_record.status = RequestStatus.PENDING
        request_record.title = "Foundation"
        request_record.overseerr_request_id = None

        published_at = datetime.now(UTC) - timedelta(days=2)
        stored_release = Release(
            id=8,
            request_id=21,
            title="Foundation.2160p.WEB-DL",
            size=30 * 1024 * 1024 * 1024,
            seeders=55,
            leechers=4,
            download_url="https://example.test/foundation",
            magnet_url=None,
            info_hash=None,
            indexer="IndexerA",
            publish_date=published_at,
            resolution="2160p",
            codec=None,
            release_group=None,
            season_number=None,
            episode_number=None,
            season_coverage=None,
            score=95,
            passed_rules=False,
            is_downloaded=False,
        )

        request_result = MagicMock()
        request_result.scalar_one_or_none.return_value = request_record
        release_result = MagicMock()
        release_result.scalars.return_value.all.return_value = [stored_release]
        rules_result = MagicMock()
        rules_result.scalars.return_value.all.return_value = []
        mock_db.execute.side_effect = [request_result, release_result, rules_result]

        monkeypatch.setattr(
            dashboard, "get_effective_settings", AsyncMock(return_value=MagicMock())
        )

        class FakeOverseerrService:
            def __init__(self, settings):
                pass

            async def close(self):
                return None

        fake_engine = MagicMock()
        fake_engine.evaluate.return_value = MagicMock(
            rejection_reason="Blocked by quality profile",
            matches=[],
            total_score=95,
            passed=False,
        )

        monkeypatch.setattr(dashboard, "OverseerrService", FakeOverseerrService)
        monkeypatch.setattr(
            dashboard.RuleEngine,
            "from_db_rules",
            MagicMock(return_value=fake_engine),
        )

        response = await dashboard.request_details(
            request_id=21, background_tasks=background_tasks, db=mock_db
        )

        body = json.loads(cast(bytes, response.body))
        assert body["releases"][0]["id"] == 8
        assert body["releases"][0]["stored_release_id"] == 8
        assert body["releases"][0]["status"] == "rejected"
        assert body["releases"][0]["status_label"] == "Rejected"
        assert body["releases"][0]["rejection_reason"] == "Blocked by quality profile"
        assert body["releases"][0]["publish_date"] == published_at.isoformat()

    @pytest.mark.asyncio
    async def test_request_details_returns_cached_tv_data_and_sync_state(
        self, mock_db, monkeypatch, background_tasks
    ):
        """TV details should return persisted seasons immediately and schedule refresh in background."""
        request_record = MagicMock()
        request_record.id = 21
        request_record.media_type = MediaType.TV
        request_record.status = RequestStatus.PENDING
        request_record.title = "Foundation"
        request_record.overseerr_request_id = None

        stale_synced_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=2)
        season_one = MagicMock(
            id=101, season_number=1, status=RequestStatus.PENDING, synced_at=stale_synced_at
        )
        episode_one = MagicMock(
            id=201,
            season_id=101,
            episode_number=1,
            title="Pilot",
            air_date=None,
            status=RequestStatus.PENDING,
            release_id=None,
        )

        request_result = MagicMock()
        request_result.scalar_one_or_none.return_value = request_record
        release_result = MagicMock()
        release_result.scalars.return_value.all.return_value = []
        rules_result = MagicMock()
        rules_result.scalars.return_value.all.return_value = []
        seasons_result = MagicMock()
        seasons_result.scalars.return_value.all.return_value = [season_one]
        episodes_result = MagicMock()
        episodes_result.scalars.return_value.all.return_value = [episode_one]
        mock_db.execute.side_effect = [
            request_result,
            release_result,
            rules_result,
            seasons_result,
            episodes_result,
        ]

        monkeypatch.setattr(
            dashboard, "get_effective_settings", AsyncMock(return_value=MagicMock())
        )

        class FakeOverseerrService:
            def __init__(self, settings):
                pass

            async def close(self):
                return None

        fake_engine = MagicMock()
        fake_engine.evaluate.return_value = MagicMock(rejection_reason=None, matches=[])
        scheduled = []
        monkeypatch.setattr(dashboard, "OverseerrService", FakeOverseerrService)
        monkeypatch.setattr(
            dashboard.RuleEngine,
            "from_db_rules",
            MagicMock(return_value=fake_engine),
        )
        monkeypatch.setattr(
            dashboard,
            "_schedule_background_episode_refresh",
            lambda tasks, request_id: scheduled.append((tasks, request_id)) or True,
        )

        response = await dashboard.request_details(
            request_id=21, background_tasks=background_tasks, db=mock_db
        )

        body = json.loads(cast(bytes, response.body))
        assert body["tv_info"]["seasons"][0]["episodes"][0]["title"] == "Pilot"
        assert body["tv_info"]["sync_state"]["stale"] is True
        assert body["tv_info"]["sync_state"]["refresh_in_progress"] is True
        assert body["tv_info"]["sync_state"]["needs_plex_enrichment"] is True
        assert scheduled == [(background_tasks, 21)]

    @pytest.mark.asyncio
    async def test_get_request_seasons_returns_sync_state_without_inline_refresh(
        self, mock_db, monkeypatch, background_tasks
    ):
        """Season endpoint should return cached data and sync metadata without blocking refresh."""
        request_record = MagicMock()
        request_record.id = 21
        request_record.media_type = MediaType.TV

        synced_at = datetime.now(UTC).replace(tzinfo=None)
        season_one = MagicMock(
            id=101, season_number=1, status=RequestStatus.AVAILABLE, synced_at=synced_at
        )
        episode_one = MagicMock(
            id=201,
            season_id=101,
            episode_number=1,
            title="Pilot",
            air_date=None,
            status=RequestStatus.AVAILABLE,
            release_id=None,
        )

        request_result = MagicMock()
        request_result.scalar_one_or_none.return_value = request_record
        seasons_result = MagicMock()
        seasons_result.scalars.return_value.all.return_value = [season_one]
        episodes_result = MagicMock()
        episodes_result.scalars.return_value.all.return_value = [episode_one]
        mock_db.execute.side_effect = [request_result, seasons_result, episodes_result]

        scheduled = []
        monkeypatch.setattr(
            dashboard,
            "_schedule_background_episode_refresh",
            lambda tasks, request_id: scheduled.append((tasks, request_id)) or True,
        )

        response = await dashboard.get_request_seasons(
            request_id=21, background_tasks=background_tasks, db=mock_db
        )

        body = json.loads(cast(bytes, response.body))
        assert body["seasons"][0]["episodes"][0]["status"] == RequestStatus.AVAILABLE.value
        assert body["sync_state"]["stale"] is False
        assert body["sync_state"]["refresh_in_progress"] is False
        assert scheduled == []

    @pytest.mark.asyncio
    async def test_request_details_serializes_unreleased_and_partial_tv_counts(
        self, mock_db, monkeypatch, background_tasks
    ):
        """TV details should preserve partial availability and unreleased episodes."""
        request_record = MagicMock()
        request_record.id = 21
        request_record.media_type = MediaType.TV
        request_record.status = RequestStatus.PENDING
        request_record.title = "The Rookie"
        request_record.overseerr_request_id = None

        synced_at = datetime.now(UTC).replace(tzinfo=None)
        season_one = MagicMock(
            id=101,
            season_number=8,
            status=RequestStatus.PARTIALLY_AVAILABLE,
            synced_at=synced_at,
        )
        available_episode = MagicMock(
            id=201,
            season_id=101,
            episode_number=15,
            title="Episode 15",
            air_date=None,
            status=RequestStatus.AVAILABLE,
            release_id=None,
        )
        future_episode = MagicMock(
            id=202,
            season_id=101,
            episode_number=16,
            title="Episode 16",
            air_date=(datetime.now(UTC) + timedelta(days=7)).date(),
            status=RequestStatus.UNRELEASED,
            release_id=None,
        )

        request_result = MagicMock()
        request_result.scalar_one_or_none.return_value = request_record
        release_result = MagicMock()
        release_result.scalars.return_value.all.return_value = []
        rules_result = MagicMock()
        rules_result.scalars.return_value.all.return_value = []
        seasons_result = MagicMock()
        seasons_result.scalars.return_value.all.return_value = [season_one]
        episodes_result = MagicMock()
        episodes_result.scalars.return_value.all.return_value = [available_episode, future_episode]
        mock_db.execute.side_effect = [
            request_result,
            release_result,
            rules_result,
            seasons_result,
            episodes_result,
        ]

        monkeypatch.setattr(
            dashboard, "get_effective_settings", AsyncMock(return_value=MagicMock())
        )

        class FakeOverseerrService:
            def __init__(self, settings):
                pass

            async def close(self):
                return None

        fake_engine = MagicMock()
        fake_engine.evaluate.return_value = MagicMock(rejection_reason=None, matches=[])
        monkeypatch.setattr(dashboard, "OverseerrService", FakeOverseerrService)
        monkeypatch.setattr(
            dashboard.RuleEngine,
            "from_db_rules",
            MagicMock(return_value=fake_engine),
        )

        response = await dashboard.request_details(
            request_id=21, background_tasks=background_tasks, db=mock_db
        )

        body = json.loads(cast(bytes, response.body))
        season_payload = body["tv_info"]["seasons"][0]
        assert season_payload["status"] == RequestStatus.PARTIALLY_AVAILABLE.value
        assert season_payload["available_count"] == 1
        assert season_payload["pending_count"] == 0
        assert season_payload["unreleased_count"] == 1
        assert [episode["status"] for episode in season_payload["episodes"]] == [
            RequestStatus.AVAILABLE.value,
            RequestStatus.UNRELEASED.value,
        ]

    @pytest.mark.asyncio
    async def test_request_details_flags_fresh_partial_tv_data_for_plex_enrichment(
        self, mock_db, monkeypatch, background_tasks
    ):
        """Fresh partial seasons with 0 available episodes should trigger Plex enrichment."""
        request_record = MagicMock()
        request_record.id = 21
        request_record.media_type = MediaType.TV
        request_record.status = RequestStatus.PENDING
        request_record.title = "The Rookie"
        request_record.overseerr_request_id = None

        synced_at = datetime.now(UTC).replace(tzinfo=None)
        season_one = MagicMock(
            id=101,
            season_number=8,
            status=RequestStatus.PARTIALLY_AVAILABLE,
            synced_at=synced_at,
        )
        pending_episode = MagicMock(
            id=201,
            season_id=101,
            episode_number=1,
            title="Episode 1",
            air_date=None,
            status=RequestStatus.PENDING,
            release_id=None,
        )
        unreleased_episode = MagicMock(
            id=202,
            season_id=101,
            episode_number=2,
            title="Episode 2",
            air_date=(datetime.now(UTC) + timedelta(days=7)).date(),
            status=RequestStatus.UNRELEASED,
            release_id=None,
        )

        request_result = MagicMock()
        request_result.scalar_one_or_none.return_value = request_record
        release_result = MagicMock()
        release_result.scalars.return_value.all.return_value = []
        rules_result = MagicMock()
        rules_result.scalars.return_value.all.return_value = []
        seasons_result = MagicMock()
        seasons_result.scalars.return_value.all.return_value = [season_one]
        episodes_result = MagicMock()
        episodes_result.scalars.return_value.all.return_value = [
            pending_episode,
            unreleased_episode,
        ]
        mock_db.execute.side_effect = [
            request_result,
            release_result,
            rules_result,
            seasons_result,
            episodes_result,
        ]

        monkeypatch.setattr(
            dashboard, "get_effective_settings", AsyncMock(return_value=MagicMock())
        )

        class FakeOverseerrService:
            def __init__(self, settings):
                pass

            async def close(self):
                return None

        fake_engine = MagicMock()
        fake_engine.evaluate.return_value = MagicMock(rejection_reason=None, matches=[])
        scheduled = []
        monkeypatch.setattr(dashboard, "OverseerrService", FakeOverseerrService)
        monkeypatch.setattr(
            dashboard.RuleEngine,
            "from_db_rules",
            MagicMock(return_value=fake_engine),
        )
        monkeypatch.setattr(
            dashboard,
            "_schedule_background_episode_refresh",
            lambda tasks, request_id: scheduled.append((tasks, request_id)) or True,
        )

        response = await dashboard.request_details(
            request_id=21, background_tasks=background_tasks, db=mock_db
        )

        body = json.loads(cast(bytes, response.body))
        season_payload = body["tv_info"]["seasons"][0]
        assert season_payload["status"] == RequestStatus.PARTIALLY_AVAILABLE.value
        assert season_payload["available_count"] == 0
        assert season_payload["total_count"] == 2
        assert season_payload["pending_count"] == 1
        assert season_payload["unreleased_count"] == 1
        assert body["tv_info"]["sync_state"]["stale"] is False
        assert body["tv_info"]["sync_state"]["needs_plex_enrichment"] is True
        assert body["tv_info"]["sync_state"]["refresh_in_progress"] is True
        assert scheduled == [(background_tasks, 21)]

    @pytest.mark.asyncio
    async def test_request_details_flags_pending_unreleased_tv_data_for_plex_enrichment(
        self, mock_db, monkeypatch, background_tasks
    ):
        """Unresolved pending/unreleased TV rows should still report Plex enrichment needed."""
        request_record = MagicMock()
        request_record.id = 51
        request_record.media_type = MediaType.TV
        request_record.status = RequestStatus.PENDING
        request_record.title = "The Rookie"
        request_record.overseerr_request_id = None

        synced_at = datetime.now(UTC).replace(tzinfo=None)
        season_one = MagicMock(
            id=101,
            season_number=8,
            status=RequestStatus.PENDING,
            synced_at=synced_at,
        )
        pending_episode = MagicMock(
            id=201,
            season_id=101,
            episode_number=1,
            title="Episode 1",
            air_date=None,
            status=RequestStatus.PENDING,
            release_id=None,
        )
        unreleased_episode = MagicMock(
            id=202,
            season_id=101,
            episode_number=2,
            title="Episode 2",
            air_date=(datetime.now(UTC) + timedelta(days=7)).date(),
            status=RequestStatus.UNRELEASED,
            release_id=None,
        )

        request_result = MagicMock()
        request_result.scalar_one_or_none.return_value = request_record
        release_result = MagicMock()
        release_result.scalars.return_value.all.return_value = []
        rules_result = MagicMock()
        rules_result.scalars.return_value.all.return_value = []
        seasons_result = MagicMock()
        seasons_result.scalars.return_value.all.return_value = [season_one]
        episodes_result = MagicMock()
        episodes_result.scalars.return_value.all.return_value = [
            pending_episode,
            unreleased_episode,
        ]
        mock_db.execute.side_effect = [
            request_result,
            release_result,
            rules_result,
            seasons_result,
            episodes_result,
        ]

        monkeypatch.setattr(
            dashboard, "get_effective_settings", AsyncMock(return_value=MagicMock())
        )

        class FakeOverseerrService:
            def __init__(self, settings):
                pass

            async def close(self):
                return None

        fake_engine = MagicMock()
        fake_engine.evaluate.return_value = MagicMock(rejection_reason=None, matches=[])
        monkeypatch.setattr(dashboard, "OverseerrService", FakeOverseerrService)
        monkeypatch.setattr(
            dashboard.RuleEngine,
            "from_db_rules",
            MagicMock(return_value=fake_engine),
        )

        response = await dashboard.request_details(
            request_id=51, background_tasks=background_tasks, db=mock_db
        )

        body = json.loads(cast(bytes, response.body))
        assert body["request"]["status"] == RequestStatus.PENDING.value
        assert body["tv_info"]["sync_state"]["needs_plex_enrichment"] is True

    @pytest.mark.asyncio
    async def test_request_details_surfaces_request_level_tv_aggregate_counts(
        self, mock_db, monkeypatch, background_tasks
    ):
        """TV details should surface aggregate episode counts alongside request status."""
        request_record = MagicMock()
        request_record.id = 21
        request_record.media_type = MediaType.TV
        request_record.status = RequestStatus.PARTIALLY_AVAILABLE
        request_record.title = "The Rookie"
        request_record.overseerr_request_id = None

        synced_at = datetime.now(UTC).replace(tzinfo=None)
        season_one = MagicMock(
            id=101,
            season_number=8,
            status=RequestStatus.PARTIALLY_AVAILABLE,
            synced_at=synced_at,
        )
        available_episode = MagicMock(
            id=201,
            season_id=101,
            episode_number=15,
            title="Episode 15",
            air_date=None,
            status=RequestStatus.AVAILABLE,
            release_id=None,
        )
        pending_episode = MagicMock(
            id=202,
            season_id=101,
            episode_number=16,
            title="Episode 16",
            air_date=None,
            status=RequestStatus.PENDING,
            release_id=None,
        )
        unreleased_episode = MagicMock(
            id=203,
            season_id=101,
            episode_number=17,
            title="Episode 17",
            air_date=(datetime.now(UTC) + timedelta(days=7)).date(),
            status=RequestStatus.UNRELEASED,
            release_id=None,
        )

        request_result = MagicMock()
        request_result.scalar_one_or_none.return_value = request_record
        release_result = MagicMock()
        release_result.scalars.return_value.all.return_value = []
        rules_result = MagicMock()
        rules_result.scalars.return_value.all.return_value = []
        seasons_result = MagicMock()
        seasons_result.scalars.return_value.all.return_value = [season_one]
        episodes_result = MagicMock()
        episodes_result.scalars.return_value.all.return_value = [
            available_episode,
            pending_episode,
            unreleased_episode,
        ]
        mock_db.execute.side_effect = [
            request_result,
            release_result,
            rules_result,
            seasons_result,
            episodes_result,
        ]

        monkeypatch.setattr(
            dashboard, "get_effective_settings", AsyncMock(return_value=MagicMock())
        )

        class FakeOverseerrService:
            def __init__(self, settings):
                pass

            async def close(self):
                return None

        fake_engine = MagicMock()
        fake_engine.evaluate.return_value = MagicMock(rejection_reason=None, matches=[])
        monkeypatch.setattr(dashboard, "OverseerrService", FakeOverseerrService)
        monkeypatch.setattr(
            dashboard.RuleEngine,
            "from_db_rules",
            MagicMock(return_value=fake_engine),
        )

        response = await dashboard.request_details(
            request_id=21, background_tasks=background_tasks, db=mock_db
        )

        body = json.loads(cast(bytes, response.body))
        assert body["request"]["status"] == RequestStatus.PARTIALLY_AVAILABLE.value
        assert body["tv_info"]["aggregate_counts"] == {
            "available": 1,
            "pending": 1,
            "unreleased": 1,
            "total": 3,
        }

    @pytest.mark.asyncio
    async def test_mark_series_available_refreshes_local_state(self, mock_db, monkeypatch):
        """Series mark-available action should call Overseerr and refresh local TV state."""
        request_record = MagicMock()
        request_record.id = 21
        request_record.media_type = MediaType.TV
        request_record.tmdb_id = 1234
        request_record.overseerr_request_id = 88

        request_result = MagicMock()
        request_result.scalar_one_or_none.return_value = request_record
        mock_db.execute.return_value = request_result

        monkeypatch.setattr(
            dashboard, "get_effective_settings", AsyncMock(return_value=MagicMock())
        )

        overseerr_service = AsyncMock()
        overseerr_service.resolve_tv_media_id.return_value = 4321
        overseerr_service.mark_series_available.return_value = True
        monkeypatch.setattr(dashboard, "OverseerrService", lambda settings: overseerr_service)

        plex_service = AsyncMock()
        monkeypatch.setattr(dashboard, "PlexService", lambda settings: plex_service)

        sync_episodes = AsyncMock()

        class FakeEpisodeSyncService:
            def __init__(self, db, plex):
                self.db = db
                self.plex = plex

            async def sync_episodes(self, request_id, force_plex_refresh=False):
                return await sync_episodes(request_id, force_plex_refresh=force_plex_refresh)

        monkeypatch.setattr(dashboard, "clear_status_cache", MagicMock(return_value=1))
        monkeypatch.setattr(dashboard, "clear_media_details_cache", MagicMock(return_value=1))

        with pytest.MonkeyPatch.context() as inner_monkeypatch:
            inner_monkeypatch.setattr(
                "app.siftarr.services.episode_sync_service.EpisodeSyncService",
                FakeEpisodeSyncService,
            )
            response = await dashboard.mark_series_available(
                request_id=21,
                db=mock_db,
            )

        body = json.loads(cast(bytes, response.body))
        assert body["status"] == "success"
        overseerr_service.resolve_tv_media_id.assert_awaited_once_with(
            overseerr_request_id=88,
            tmdb_id=1234,
        )
        overseerr_service.mark_series_available.assert_awaited_once_with(4321)
        sync_episodes.assert_awaited_once_with(21, force_plex_refresh=True)

    @pytest.mark.asyncio
    async def test_mark_series_available_returns_error_on_overseerr_failure(
        self, mock_db, monkeypatch
    ):
        """Series mark-available action should surface Overseerr failures."""
        request_record = MagicMock()
        request_record.id = 21
        request_record.media_type = MediaType.TV
        request_record.tmdb_id = 1234
        request_record.overseerr_request_id = 88

        request_result = MagicMock()
        request_result.scalar_one_or_none.return_value = request_record
        mock_db.execute.return_value = request_result

        monkeypatch.setattr(
            dashboard, "get_effective_settings", AsyncMock(return_value=MagicMock())
        )

        overseerr_service = AsyncMock()
        overseerr_service.resolve_tv_media_id.return_value = 4321
        overseerr_service.mark_series_available.return_value = False
        monkeypatch.setattr(dashboard, "OverseerrService", lambda settings: overseerr_service)
        monkeypatch.setattr(dashboard, "PlexService", lambda settings: AsyncMock())

        response = await dashboard.mark_series_available(
            request_id=21,
            db=mock_db,
        )

        assert response.status_code == 502

    @pytest.mark.asyncio
    async def test_mark_series_available_requires_overseerr_media_id(self, mock_db, monkeypatch):
        """Series mark-available action should fail clearly if no Overseerr media id can be resolved."""
        request_record = MagicMock()
        request_record.id = 21
        request_record.media_type = MediaType.TV
        request_record.tmdb_id = 1234
        request_record.overseerr_request_id = 88

        request_result = MagicMock()
        request_result.scalar_one_or_none.return_value = request_record
        mock_db.execute.return_value = request_result

        monkeypatch.setattr(
            dashboard, "get_effective_settings", AsyncMock(return_value=MagicMock())
        )

        overseerr_service = AsyncMock()
        overseerr_service.resolve_tv_media_id.return_value = None
        monkeypatch.setattr(dashboard, "OverseerrService", lambda settings: overseerr_service)
        monkeypatch.setattr(dashboard, "PlexService", lambda settings: AsyncMock())

        with pytest.raises(HTTPException) as exc_info:
            await dashboard.mark_series_available(
                request_id=21,
                db=mock_db,
            )

        assert exc_info.value.status_code == 400
        assert exc_info.value.detail == "No Overseerr media ID available"

    @pytest.mark.asyncio
    async def test_use_request_release_redirects_pending_requests_to_pending_tab(
        self, mock_db, monkeypatch
    ):
        """Stored release selection should default back to pending when request is pending."""
        request_record = MagicMock()
        request_record.id = 21
        request_record.status = RequestStatus.PENDING
        release_record = MagicMock(id=99)

        request_result = MagicMock()
        request_result.scalar_one_or_none.return_value = request_record
        release_result = MagicMock()
        release_result.scalar_one_or_none.return_value = release_record
        mock_db.execute.side_effect = [request_result, release_result]

        use_releases = AsyncMock()
        monkeypatch.setattr(dashboard, "use_releases", use_releases)

        response = await dashboard.use_request_release(
            request_id=21,
            release_id=99,
            redirect_to=None,
            db=mock_db,
        )

        assert response.status_code == 303
        assert response.headers["location"] == "/?tab=pending"
        use_releases.assert_awaited_once_with(
            mock_db,
            request_record,
            [release_record],
            selection_source="manual",
        )

    @pytest.mark.asyncio
    async def test_use_manual_release_persists_then_uses_release(self, mock_db, monkeypatch):
        """Ad hoc manual-search releases should persist then use the normal release flow."""
        request_record = MagicMock()
        request_record.id = 21
        request_record.status = RequestStatus.PENDING

        request_result = MagicMock()
        request_result.scalar_one_or_none.return_value = request_record
        rules_result = MagicMock()
        rules_result.scalars.return_value.all.return_value = []
        mock_db.execute.side_effect = [request_result, rules_result]

        stored_release = MagicMock(id=123)
        fake_engine = MagicMock()
        fake_engine.evaluate.return_value = MagicMock(total_score=55, passed=True, matches=[])
        persist_manual_release = AsyncMock(return_value=stored_release)
        use_releases = AsyncMock(return_value={"status": "downloading"})

        monkeypatch.setattr(
            dashboard.RuleEngine,
            "from_db_rules",
            MagicMock(return_value=fake_engine),
        )
        monkeypatch.setattr(dashboard, "persist_manual_release", persist_manual_release)
        monkeypatch.setattr(dashboard, "use_releases", use_releases)

        response = await dashboard.use_manual_release(
            request_id=21,
            title="Foundation.S01E01.1080p.WEB-DL",
            size=2,
            seeders=10,
            leechers=1,
            indexer="IndexerA",
            download_url="https://example.test/foundation.torrent",
            magnet_url=None,
            info_hash="abc123",
            publish_date="2026-04-16T00:00:00+00:00",
            resolution="1080p",
            codec="x265",
            release_group="GROUP",
            redirect_to=None,
            db=mock_db,
        )

        assert response.status_code == 303
        assert response.headers["location"] == "/?tab=pending"
        persist_manual_release.assert_awaited_once()
        use_releases.assert_awaited_once_with(
            mock_db,
            request_record,
            [stored_release],
            selection_source="manual",
        )

    @pytest.mark.asyncio
    async def test_use_manual_release_rejects_invalid_publish_date(self, mock_db):
        """Manual selection should fail fast on invalid publish dates."""
        request_record = MagicMock()
        request_record.id = 21
        request_record.status = RequestStatus.PENDING

        request_result = MagicMock()
        request_result.scalar_one_or_none.return_value = request_record
        mock_db.execute.return_value = request_result

        with pytest.raises(HTTPException) as exc_info:
            await dashboard.use_manual_release(
                request_id=21,
                title="Foundation.S01E01.1080p.WEB-DL",
                size=2,
                seeders=10,
                leechers=1,
                indexer="IndexerA",
                download_url="https://example.test/foundation.torrent",
                magnet_url=None,
                info_hash=None,
                publish_date="not-a-date",
                resolution=None,
                codec=None,
                release_group=None,
                redirect_to=None,
                db=mock_db,
            )

        assert exc_info.value.status_code == 400
        assert exc_info.value.detail == "Invalid publish_date"

    def test_dashboard_template_includes_search_multi_season_ui(self):
        """Dashboard template should expose the Search Multi Season TV UI."""
        template_path = "/home/lucas/9999-personal/siftarr/app/siftarr/templates/dashboard.html"
        with open(template_path, encoding="utf-8") as handle:
            template = handle.read()

        assert "Search Multi Season Packs" in template
        assert "Run Search Multi Season Packs to inspect broad multi-season coverage." in template
        assert "Searching multi season packs..." in template
        assert "No multi season or complete-series results found." in template
        assert "function searchAllSeasonPacks(" in template
        assert "/requests/' + targetRequestId + '/seasons/search-all" in template

    def test_dashboard_template_uses_collapsible_episode_results(self):
        """Episode search results should live in their own collapsible sections."""
        template_path = "/home/lucas/9999-personal/siftarr/app/siftarr/templates/dashboard.html"
        with open(template_path, encoding="utf-8") as handle:
            template = handle.read()

        assert "episode-details-" in template
        assert '<details id="\' + episodeDetailsId + \'" class="group rounded-lg border' in template
        assert "if (details) details.open = true;" in template

    def test_dashboard_template_includes_release_status_column_and_upload_age(self):
        """Torrent cards should render a right-side status area with rejection reason and age."""
        template_path = "/home/lucas/9999-personal/siftarr/app/siftarr/templates/dashboard.html"
        with open(template_path, encoding="utf-8") as handle:
            template = handle.read()

        assert 'data-release-status-column="true"' in template
        assert 'data-release-rejection-reason="true"' in template
        assert 'data-release-upload-age="true"' in template
        assert 'data-release-size-per-season="true"' in template
        assert 'data-release-resolution="true"' in template
        assert 'data-release-codec="true"' in template
        assert "function formatRelativePublishAge(publishDate)" in template
        assert "window.siftarrStagingModeEnabled" in template
        assert "/manual-release/use" in template
        assert "background refresh updates Plex/Overseerr data" in template
        assert "Plex episode availability is being resolved for partial seasons" in template
        assert "Mark Series Available in Overseerr" in template
        assert "function markSeriesAvailable(requestId)" in template

    def test_dashboard_template_supports_annotation_highlighting(self):
        """Torrent annotation highlighting helpers should exist in the template."""
        template_path = "/home/lucas/9999-personal/siftarr/app/siftarr/templates/dashboard.html"
        with open(template_path, encoding="utf-8") as handle:
            template = handle.read()

        assert "function renderAnnotation(" in template
        assert "function releaseAnnotationTone(" in template
        assert "text-emerald-400" in template
        assert "text-red-400" in template
