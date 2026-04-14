"""Tests for release selection helpers."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.siftarr.models.request import MediaType, Request, RequestStatus
from app.siftarr.services import release_selection_service
from app.siftarr.services.prowlarr_service import ProwlarrRelease
from app.siftarr.services.rule_engine import ReleaseEvaluation


class TestReleaseSelectionService:
    """Focused tests for staging-mode release selection."""

    @pytest.fixture
    def mock_db(self):
        """Create a mock database session."""
        return AsyncMock()

    @pytest.fixture
    def request_record(self):
        """Create a mock request record."""
        request = MagicMock(spec=Request)
        request.id = 7
        request.media_type = MediaType.MOVIE
        request.status = RequestStatus.PENDING
        return request

    @pytest.fixture
    def selected_release(self):
        """Create a mock user-selected release."""
        release = MagicMock()
        release.id = 100
        release.title = "User Pick"
        release.score = 50
        release.size = 1_500_000_000
        release.seeders = 25
        release.leechers = 3
        release.indexer = "Indexer A"
        release.magnet_url = "magnet:?xt=urn:btih:userpick"
        release.download_url = "https://example.com/user-pick.torrent"
        release.info_hash = None
        release.publish_date = None
        release.resolution = None
        release.codec = None
        release.release_group = None
        return release

    @pytest.mark.asyncio
    async def test_use_releases_marks_manual_selection_source(
        self,
        mock_db,
        request_record,
        selected_release,
    ):
        """Manual release picks should stage as manual selections."""
        settings = MagicMock(staging_mode_enabled=True)
        queue_service = AsyncMock()
        staging_service = AsyncMock()
        staged_record = MagicMock(id=33)
        staging_service.save_release.return_value = staged_record

        existing_result = MagicMock()
        existing_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = existing_result

        with pytest.MonkeyPatch.context() as monkeypatch:
            monkeypatch.setattr(
                release_selection_service,
                "get_effective_settings",
                AsyncMock(return_value=settings),
            )
            monkeypatch.setattr(
                release_selection_service,
                "PendingQueueService",
                MagicMock(return_value=queue_service),
            )
            monkeypatch.setattr(
                release_selection_service,
                "StagingService",
                MagicMock(return_value=staging_service),
            )

            result = await release_selection_service.use_releases(
                mock_db,
                request_record,
                [selected_release],
                selection_source="manual",
            )

        assert result["status"] == "staged"
        staging_service.save_release.assert_awaited_once()
        assert staging_service.save_release.await_args.kwargs["selection_source"] == "manual"
        queue_service.remove_from_queue.assert_awaited_once_with(request_record.id)

    @pytest.mark.asyncio
    async def test_use_releases_keeps_existing_staged_release(
        self, mock_db, request_record, selected_release
    ):
        """Already staged releases should be reused instead of staged again."""
        settings = MagicMock(staging_mode_enabled=True)
        queue_service = AsyncMock()
        staging_service = AsyncMock()

        existing_stage = MagicMock(id=44)
        existing_result = MagicMock()
        existing_result.scalar_one_or_none.return_value = existing_stage
        mock_db.execute.return_value = existing_result

        with pytest.MonkeyPatch.context() as monkeypatch:
            monkeypatch.setattr(
                release_selection_service,
                "get_effective_settings",
                AsyncMock(return_value=settings),
            )
            monkeypatch.setattr(
                release_selection_service,
                "PendingQueueService",
                MagicMock(return_value=queue_service),
            )
            monkeypatch.setattr(
                release_selection_service,
                "StagingService",
                MagicMock(return_value=staging_service),
            )

            result = await release_selection_service.use_releases(
                mock_db,
                request_record,
                [selected_release],
                selection_source="rule",
            )

        assert result["status"] == "staged"
        assert result["staged_ids"] == [existing_stage.id]
        staging_service.save_release.assert_not_awaited()
        queue_service.remove_from_queue.assert_awaited_once_with(request_record.id)

    @pytest.mark.asyncio
    async def test_store_search_results_persists_multi_season_coverage(self, mock_db):
        """Multi-season packs should persist exact covered seasons."""
        mock_db.add = MagicMock()
        mock_db.commit = AsyncMock()
        mock_db.refresh = AsyncMock()

        release = ProwlarrRelease(
            title="Show.S01-S03.2160p.WEB-DL",
            size=30 * 1024 * 1024 * 1024,
            seeders=50,
            leechers=4,
            download_url="https://example.test/show-s01-s03.torrent",
            indexer="IndexerA",
        )
        evaluation = ReleaseEvaluation(release=release, passed=True, total_score=95, matches=[])

        await release_selection_service.store_search_results(mock_db, 12, [evaluation])

        stored_record = mock_db.add.call_args.args[0]
        assert stored_record.request_id == 12
        assert stored_record.season_number == 1
        assert stored_record.episode_number is None
        assert stored_record.season_coverage == "1,2,3"
        mock_db.commit.assert_awaited_once()
        mock_db.refresh.assert_awaited_once_with(stored_record)

    @pytest.mark.asyncio
    async def test_store_search_results_persists_complete_series_marker(self, mock_db):
        """Complete-series releases should persist a reusable broad coverage marker."""
        mock_db.add = MagicMock()
        mock_db.commit = AsyncMock()
        mock_db.refresh = AsyncMock()

        release = ProwlarrRelease(
            title="Show.Complete.Series.1080p.BluRay",
            size=42 * 1024 * 1024 * 1024,
            seeders=77,
            leechers=2,
            download_url="https://example.test/show-complete-series.torrent",
            indexer="IndexerB",
        )
        evaluation = ReleaseEvaluation(release=release, passed=True, total_score=88, matches=[])

        await release_selection_service.store_search_results(mock_db, 33, [evaluation])

        stored_record = mock_db.add.call_args.args[0]
        assert stored_record.request_id == 33
        assert stored_record.season_number is None
        assert stored_record.episode_number is None
        assert stored_record.season_coverage == "*"
