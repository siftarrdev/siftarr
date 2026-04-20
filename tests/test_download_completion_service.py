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
        mock_plex_polling._check_movie = AsyncMock(return_value=None)

        # Reload request with selectinload
        req_result = MagicMock()
        req_result.scalar_one_or_none.return_value = request
        mock_db.execute.side_effect = [
            _rows_result([(torrent, request)]),
            req_result,
        ]

        service = DownloadCompletionService(mock_db, mock_qbit, mock_plex_polling)
        result = await service.check_downloading_requests()
        # Plex returned None, so not completed
        assert result == 0
        mock_plex_polling._check_movie.assert_called_once()

    @pytest.mark.asyncio
    async def test_plex_confirms_completion(self, mock_db, mock_qbit, mock_plex_polling):
        """When Plex confirms, request is reconciled through Plex polling."""
        from app.siftarr.models.request import MediaType, RequestStatus
        from app.siftarr.services.plex_polling_service import PollDecision

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

        decision = PollDecision(request_id=10, reason="Found on Plex")
        mock_plex_polling._check_movie = AsyncMock(return_value=decision)
        mock_plex_polling._apply_decision = AsyncMock()
        mock_qbit.get_torrent_info = AsyncMock(return_value=None)

        req_result = MagicMock()
        req_result.scalar_one_or_none.return_value = request
        mock_db.execute.side_effect = [
            _rows_result([(torrent, request)]),
            req_result,
        ]

        service = DownloadCompletionService(mock_db, mock_qbit, mock_plex_polling)
        result = await service.check_downloading_requests()

        assert result == 1
        mock_plex_polling._apply_decision.assert_called_once()

    @pytest.mark.asyncio
    async def test_tv_completion_uses_reconcile_path_for_available_show(
        self, mock_db, mock_qbit, mock_plex_polling
    ):
        """TV download completion should reuse the Plex reconciliation path, not force completed."""
        from app.siftarr.models.request import MediaType, RequestStatus
        from app.siftarr.services.plex_polling_service import PollDecision

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

        decision = PollDecision(
            request_id=10,
            reason="All episodes found on Plex",
            requested_episode_count=2,
            completed_episodes=frozenset({(1, 1), (1, 2)}),
            episode_availability={(1, 1): True, (1, 2): True, (2, 1): False},
        )
        mock_plex_polling._check_tv = AsyncMock(return_value=decision)
        mock_plex_polling._apply_decision = AsyncMock()
        mock_qbit.get_torrent_info = AsyncMock(return_value=None)

        req_result = MagicMock()
        req_result.scalar_one_or_none.return_value = request
        mock_db.execute.side_effect = [
            _rows_result([(torrent, request)]),
            req_result,
        ]

        service = DownloadCompletionService(mock_db, mock_qbit, mock_plex_polling)
        result = await service.check_downloading_requests()

        assert result == 1
        mock_plex_polling._check_tv.assert_called_once_with(request)
        mock_plex_polling._apply_decision.assert_called_once_with(request, decision)

    @pytest.mark.asyncio
    async def test_logs_plex_reconcile_reason_for_completed_request(
        self, mock_db, mock_qbit, mock_plex_polling, caplog
    ):
        """Completion logging should explain the Plex reconciliation outcome."""
        from app.siftarr.models.request import MediaType, RequestStatus
        from app.siftarr.services.plex_polling_service import PollDecision

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

        decision = PollDecision(
            request_id=10,
            reason="All episodes found on Plex",
            requested_episode_count=1,
            completed_episodes=frozenset({(1, 1)}),
            episode_availability={(1, 1): True},
        )
        mock_plex_polling._check_tv = AsyncMock(return_value=decision)
        mock_plex_polling._apply_decision = AsyncMock()
        mock_qbit.get_torrent_info = AsyncMock(return_value=None)

        req_result = MagicMock()
        req_result.scalar_one_or_none.return_value = request
        mock_db.execute.side_effect = [_rows_result([(torrent, request)]), req_result]

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

        available_torrent = MagicMock()
        available_torrent.id = 1
        available_torrent.request_id = 10
        available_torrent.title = "Already Available"
        available_torrent.magnet_url = (
            "magnet:?xt=urn:btih:da39a3ee5e6b4b0d3255bfef95601890afd80709"
        )

        available_request = MagicMock()
        available_request.id = 10
        available_request.title = "Already Available"
        available_request.media_type = MediaType.MOVIE
        available_request.status = RequestStatus.AVAILABLE

        partial_torrent = MagicMock()
        partial_torrent.id = 2
        partial_torrent.request_id = 11
        partial_torrent.title = "Partially Available"
        partial_torrent.magnet_url = "magnet:?xt=urn:btih:ea39a3ee5e6b4b0d3255bfef95601890afd80709"

        partial_request = MagicMock()
        partial_request.id = 11
        partial_request.title = "Partially Available"
        partial_request.media_type = MediaType.TV
        partial_request.status = RequestStatus.PARTIALLY_AVAILABLE

        mock_db.execute.return_value = _rows_result(
            [
                (available_torrent, available_request),
                (partial_torrent, partial_request),
            ]
        )

        service = DownloadCompletionService(mock_db, mock_qbit, mock_plex_polling)
        result = await service.check_downloading_requests()

        assert result == 0
        mock_qbit.get_torrent_info.assert_not_called()
        mock_plex_polling._check_movie.assert_not_called()
        mock_plex_polling._check_tv.assert_not_called()
