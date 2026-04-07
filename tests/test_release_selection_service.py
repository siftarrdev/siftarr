"""Tests for release selection helpers."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.siftarr.models.request import MediaType, Request, RequestStatus
from app.siftarr.services import release_selection_service


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
