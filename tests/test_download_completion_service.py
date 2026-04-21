"""Tests for DownloadCompletionService."""

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.siftarr.services.download_completion_service import (
    DownloadCompletionService,
    _extract_hash,
)


def _rows_result(rows: list) -> MagicMock:
    """Create a mock execute result that returns rows from .all()."""
    result = MagicMock()
    result.all.return_value = rows
    return result


def _request_id_rows(request_ids: list[int]) -> MagicMock:
    """Create a mock execute result that returns request_id tuples from .all()."""
    result = MagicMock()
    result.all.return_value = [(request_id,) for request_id in request_ids]
    return result


class TestExtractHash:
    def test_extracts_hex_hash(self):
        magnet = "magnet:?xt=urn:btih:da39a3ee5e6b4b0d3255bfef95601890afd80709&dn=test"
        assert _extract_hash(magnet) == "da39a3ee5e6b4b0d3255bfef95601890afd80709"

    def test_returns_none_for_none(self):
        assert _extract_hash(None) is None

    def test_returns_none_when_no_btih(self):
        assert _extract_hash("magnet:?xt=urn:other:abc") is None


class TestDownloadCompletionService:
    @pytest.fixture
    def mock_db(self):
        db = AsyncMock()
        db.execute = AsyncMock()
        db.add = MagicMock()
        db.flush = AsyncMock()
        db.commit = AsyncMock()
        return db

    @pytest.fixture
    def mock_qbit(self):
        return AsyncMock()

    @pytest.fixture
    def mock_plex_polling(self):
        return AsyncMock()

    @pytest.mark.asyncio
    async def test_no_downloading_torrents_returns_zero(
        self, mock_db, mock_qbit, mock_plex_polling
    ):
        """When there are no approved torrents, return 0."""
        mock_db.execute.return_value = _rows_result([])

        service = DownloadCompletionService(mock_db, mock_qbit, mock_plex_polling)
        result = await service.check_downloading_requests()
        assert result == 0

    @pytest.mark.asyncio
    async def test_torrent_not_in_qbit_treated_as_done(self, mock_db, mock_qbit, mock_plex_polling):
        """A torrent not found in qBit is treated as completed."""
        from app.siftarr.models.request import MediaType, RequestStatus
        from app.siftarr.services.plex_polling_service import TargetedReconcileResult

        torrent = MagicMock()
        torrent.id = 1
        torrent.request_id = 10
        torrent.title = "Test Movie 2020"
        torrent.magnet_url = "magnet:?xt=urn:btih:da39a3ee5e6b4b0d3255bfef95601890afd80709"

        request = MagicMock()
        request.id = 10
        request.title = "Test Movie 2020"
        request.media_type = MediaType.MOVIE
        request.status = RequestStatus.DOWNLOADING

        # qBit returns None (not found)
        mock_qbit.get_torrent_info = AsyncMock(return_value=None)

        # Plex not found either
        mock_plex_polling.reconcile_request = AsyncMock(
            return_value=TargetedReconcileResult(
                request_id=10,
                matched=False,
                reconciled=False,
                status_before=RequestStatus.DOWNLOADING,
                status_after=RequestStatus.DOWNLOADING,
            )
        )

        mock_db.execute.side_effect = [
            _rows_result([(torrent, request)]),
            _request_id_rows([]),
        ]

        service = DownloadCompletionService(mock_db, mock_qbit, mock_plex_polling)
        result = await service.check_downloading_requests()
        # Plex returned None, so not completed
        assert result == 0
        mock_plex_polling.reconcile_request.assert_called_once_with(10)

    @pytest.mark.asyncio
    async def test_plex_confirms_completion(self, mock_db, mock_qbit, mock_plex_polling):
        """When Plex confirms, request is reconciled through Plex polling."""
        from app.siftarr.models.request import MediaType, RequestStatus
        from app.siftarr.services.plex_polling_service import TargetedReconcileResult

        torrent = MagicMock()
        torrent.id = 1
        torrent.request_id = 10
        torrent.title = "Test Movie 2020"
        torrent.magnet_url = "magnet:?xt=urn:btih:da39a3ee5e6b4b0d3255bfef95601890afd80709"

        request = MagicMock()
        request.id = 10
        request.title = "Test Movie 2020"
        request.media_type = MediaType.MOVIE
        request.status = RequestStatus.DOWNLOADING

        mock_plex_polling.reconcile_request = AsyncMock(
            return_value=TargetedReconcileResult(
                request_id=10,
                matched=True,
                reconciled=True,
                status_before=RequestStatus.DOWNLOADING,
                status_after=RequestStatus.COMPLETED,
                reason="Found on Plex",
            )
        )
        mock_qbit.get_torrent_info = AsyncMock(return_value=None)

        mock_db.execute.side_effect = [
            _rows_result([(torrent, request)]),
            _request_id_rows([]),
        ]

        service = DownloadCompletionService(mock_db, mock_qbit, mock_plex_polling)
        result = await service.check_downloading_requests()

        assert result == 1
        mock_plex_polling.reconcile_request.assert_called_once_with(10)

    @pytest.mark.asyncio
    async def test_tv_completion_uses_reconcile_path_for_completed_show(
        self, mock_db, mock_qbit, mock_plex_polling
    ):
        """TV download completion should reuse the Plex reconciliation path, not force completed."""
        from app.siftarr.models.request import MediaType, RequestStatus
        from app.siftarr.services.plex_polling_service import TargetedReconcileResult

        torrent = MagicMock()
        torrent.id = 1
        torrent.request_id = 10
        torrent.title = "Test Show S01"
        torrent.magnet_url = "magnet:?xt=urn:btih:da39a3ee5e6b4b0d3255bfef95601890afd80709"

        request = MagicMock()
        request.id = 10
        request.title = "Test Show"
        request.media_type = MediaType.TV
        request.status = RequestStatus.DOWNLOADING

        mock_plex_polling.reconcile_request = AsyncMock(
            return_value=TargetedReconcileResult(
                request_id=10,
                matched=True,
                reconciled=True,
                status_before=RequestStatus.DOWNLOADING,
                status_after=RequestStatus.COMPLETED,
                reason="All episodes found on Plex",
                requested_episode_count=2,
                completed_episodes=frozenset({(1, 1), (1, 2)}),
            )
        )
        mock_qbit.get_torrent_info = AsyncMock(return_value=None)

        mock_db.execute.side_effect = [
            _rows_result([(torrent, request)]),
            _request_id_rows([]),
        ]

        service = DownloadCompletionService(mock_db, mock_qbit, mock_plex_polling)
        result = await service.check_downloading_requests()

        assert result == 1
        mock_plex_polling.reconcile_request.assert_called_once_with(10)

    @pytest.mark.asyncio
    async def test_logs_plex_reconcile_reason_for_completed_request(
        self, mock_db, mock_qbit, mock_plex_polling, caplog
    ):
        """Completion logging should explain the Plex reconciliation outcome."""
        from app.siftarr.models.request import MediaType, RequestStatus
        from app.siftarr.services.plex_polling_service import TargetedReconcileResult

        torrent = MagicMock()
        torrent.id = 1
        torrent.request_id = 10
        torrent.title = "Test Show S01"
        torrent.magnet_url = "magnet:?xt=urn:btih:da39a3ee5e6b4b0d3255bfef95601890afd80709"

        request = MagicMock()
        request.id = 10
        request.title = "Test Show"
        request.media_type = MediaType.TV
        request.status = RequestStatus.DOWNLOADING

        mock_plex_polling.reconcile_request = AsyncMock(
            return_value=TargetedReconcileResult(
                request_id=10,
                matched=True,
                reconciled=True,
                status_before=RequestStatus.DOWNLOADING,
                status_after=RequestStatus.COMPLETED,
                reason="All episodes found on Plex",
                requested_episode_count=1,
                completed_episodes=frozenset({(1, 1)}),
            )
        )
        mock_qbit.get_torrent_info = AsyncMock(return_value=None)

        mock_db.execute.side_effect = [
            _rows_result([(torrent, request)]),
            _request_id_rows([]),
        ]

        service = DownloadCompletionService(mock_db, mock_qbit, mock_plex_polling)

        with caplog.at_level(logging.INFO):
            result = await service.check_downloading_requests()

        assert result == 1
        assert (
            "DownloadCompletionService: reconciled request_id=10 title=Test Show via Plex "
            "(All episodes found on Plex)" in caplog.text
        )

    @pytest.mark.asyncio
    async def test_ignores_resolved_requests_even_if_row_is_returned(
        self, mock_db, mock_qbit, mock_plex_polling
    ):
        """Resolved requests should not be treated as active downloads."""
        from app.siftarr.models.request import MediaType, RequestStatus

        completed_torrent = MagicMock()
        completed_torrent.id = 1
        completed_torrent.request_id = 10
        completed_torrent.title = "Already Completed"
        completed_torrent.magnet_url = (
            "magnet:?xt=urn:btih:da39a3ee5e6b4b0d3255bfef95601890afd80709"
        )

        completed_request = MagicMock()
        completed_request.id = 10
        completed_request.title = "Already Completed"
        completed_request.media_type = MediaType.MOVIE
        completed_request.status = RequestStatus.COMPLETED

        pending_torrent = MagicMock()
        pending_torrent.id = 2
        pending_torrent.request_id = 11
        pending_torrent.title = "Pending Request"
        pending_torrent.magnet_url = "magnet:?xt=urn:btih:ea39a3ee5e6b4b0d3255bfef95601890afd80709"

        pending_request = MagicMock()
        pending_request.id = 11
        pending_request.title = "Pending Request"
        pending_request.media_type = MediaType.TV
        pending_request.status = RequestStatus.PENDING

        mock_db.execute.return_value = _rows_result(
            [
                (completed_torrent, completed_request),
                (pending_torrent, pending_request),
            ]
        )

        service = DownloadCompletionService(mock_db, mock_qbit, mock_plex_polling)
        result = await service.check_downloading_requests()

        assert result == 0
        mock_qbit.get_torrent_info.assert_not_called()
        mock_plex_polling.reconcile_request.assert_not_called()

    @pytest.mark.asyncio
    async def test_incomplete_torrent_does_not_trigger_plex_reconciliation(
        self, mock_db, mock_qbit, mock_plex_polling
    ):
        """Incomplete torrents should not call the shared Plex reconciliation path."""
        from app.siftarr.models.request import MediaType, RequestStatus

        torrent = MagicMock()
        torrent.id = 1
        torrent.request_id = 10
        torrent.title = "Test Show S01E01"
        torrent.magnet_url = "magnet:?xt=urn:btih:da39a3ee5e6b4b0d3255bfef95601890afd80709"

        request = MagicMock()
        request.id = 10
        request.title = "Test Show"
        request.media_type = MediaType.TV
        request.status = RequestStatus.DOWNLOADING

        mock_qbit.get_torrent_info = AsyncMock(
            return_value={"progress": 0.4, "state": "downloading"}
        )
        mock_db.execute.side_effect = [_rows_result([(torrent, request)])]

        service = DownloadCompletionService(mock_db, mock_qbit, mock_plex_polling)
        result = await service.check_downloading_requests()

        assert result == 0
        mock_plex_polling.reconcile_request.assert_not_called()

    @pytest.mark.asyncio
    async def test_tv_request_reconciles_when_any_approved_torrent_finishes(
        self, mock_db, mock_qbit, mock_plex_polling
    ):
        """A completed TV episode should trigger Plex reconciliation even if siblings still download."""
        from app.siftarr.models.request import MediaType, RequestStatus
        from app.siftarr.services.plex_polling_service import TargetedReconcileResult

        completed_torrent = MagicMock()
        completed_torrent.id = 1
        completed_torrent.request_id = 10
        completed_torrent.title = "Test Show S01E01"
        completed_torrent.magnet_url = (
            "magnet:?xt=urn:btih:da39a3ee5e6b4b0d3255bfef95601890afd80709"
        )

        downloading_torrent = MagicMock()
        downloading_torrent.id = 2
        downloading_torrent.request_id = 10
        downloading_torrent.title = "Test Show S01E02"
        downloading_torrent.magnet_url = (
            "magnet:?xt=urn:btih:ea39a3ee5e6b4b0d3255bfef95601890afd80709"
        )

        request = MagicMock()
        request.id = 10
        request.title = "Test Show"
        request.media_type = MediaType.TV
        request.status = RequestStatus.DOWNLOADING

        mock_qbit.get_torrent_info = AsyncMock(
            side_effect=[
                {"progress": 1.0, "state": "uploading"},
                {"progress": 0.5, "state": "downloading"},
            ]
        )
        mock_plex_polling.reconcile_request = AsyncMock(
            return_value=TargetedReconcileResult(
                request_id=10,
                matched=True,
                reconciled=True,
                status_before=RequestStatus.DOWNLOADING,
                status_after=RequestStatus.PENDING,
                reason="Episode found on Plex",
                requested_episode_count=2,
                completed_episodes=frozenset({(1, 1)}),
            )
        )
        mock_db.execute.side_effect = [
            _rows_result([(completed_torrent, request), (downloading_torrent, request)]),
            _request_id_rows([]),
        ]

        service = DownloadCompletionService(mock_db, mock_qbit, mock_plex_polling)
        result = await service.check_downloading_requests()

        assert result == 1
        mock_plex_polling.reconcile_request.assert_called_once_with(10)

    @pytest.mark.asyncio
    async def test_download_completed_log_is_deduplicated(
        self, mock_db, mock_qbit, mock_plex_polling
    ):
        """Existing download_completed activity should not be logged again."""
        from app.siftarr.models.request import MediaType, RequestStatus
        from app.siftarr.services.plex_polling_service import TargetedReconcileResult

        torrent = MagicMock()
        torrent.id = 1
        torrent.request_id = 10
        torrent.title = "Test Show S01E01"
        torrent.magnet_url = "magnet:?xt=urn:btih:da39a3ee5e6b4b0d3255bfef95601890afd80709"

        request = MagicMock()
        request.id = 10
        request.title = "Test Show"
        request.media_type = MediaType.TV
        request.status = RequestStatus.DOWNLOADING

        mock_qbit.get_torrent_info = AsyncMock(return_value={"progress": 1.0, "state": "uploading"})
        mock_plex_polling.reconcile_request = AsyncMock(
            return_value=TargetedReconcileResult(
                request_id=10,
                matched=False,
                reconciled=False,
                status_before=RequestStatus.DOWNLOADING,
                status_after=RequestStatus.DOWNLOADING,
            )
        )
        mock_db.execute.side_effect = [
            _rows_result([(torrent, request)]),
            _request_id_rows([10]),
        ]

        service = DownloadCompletionService(mock_db, mock_qbit, mock_plex_polling)
        result = await service.check_downloading_requests()

        assert result == 0
        mock_db.add.assert_not_called()
