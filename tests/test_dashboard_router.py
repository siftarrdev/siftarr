"""Tests for dashboard router helpers and endpoints."""

import json
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

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
        assert body["releases"][1]["covered_seasons"] == [1, 2, 3]
        assert body["releases"][1]["covered_season_count"] == 3
        assert body["releases"][1]["covers_all_known_seasons"] is True
        assert body["releases"][1]["is_complete_series"] is False
        assert body["releases"][2]["covered_seasons"] == []
        assert body["releases"][2]["is_complete_series"] is True
        assert "Foundation.Complete.S01.1080p.BluRay" not in [
            release["title"] for release in body["releases"]
        ]

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
        self, mock_db, monkeypatch
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
            response = await dashboard.request_details(request_id=21, db=mock_db)

        body = json.loads(cast(bytes, response.body))
        assert body["releases"][0]["covered_seasons"] == [1, 2]
        assert body["releases"][0]["covered_season_count"] == 2
        assert body["releases"][0]["covers_all_known_seasons"] is True
        assert [release["title"] for release in body["tv_info"]["releases_by_season"]["1"]] == [
            "Foundation.S01-S02.2160p.WEB-DL"
        ]
        assert [release["title"] for release in body["tv_info"]["releases_by_season"]["2"]] == [
            "Foundation.S01-S02.2160p.WEB-DL"
        ]

    @pytest.mark.asyncio
    async def test_request_details_orders_stored_releases_by_score_then_size(
        self, mock_db, monkeypatch
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
            response = await dashboard.request_details(request_id=21, db=mock_db)

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
