"""Tests for DownloadCompletionService."""

import json
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.siftarr.services.lifecycle.download_completion_service import (
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
    result.all.return_value = [
        (request_id, json.dumps({"done_torrents": [{"torrent_id": 1}]}))
        for request_id in request_ids
    ]
    return result


def _completion_log_rows(rows: list[tuple[int, dict]]) -> MagicMock:
    result = MagicMock()
    result.all.return_value = [(request_id, json.dumps(details)) for request_id, details in rows]
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

        # Support async with db.begin_nested() (ActivityLogService uses savepoints).
        nested_context = AsyncMock()
        nested_context.__aenter__.return_value = None
        nested_context.__aexit__.return_value = None
        db.begin_nested = MagicMock(return_value=nested_context)

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
        from app.siftarr.services.admin.plex_polling_service import CheckRequestResult

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

        # Batch-fetch returns empty (torrent not found in qBit)
        mock_qbit.get_all_active_torrents = AsyncMock(return_value=[])

        # Plex not found either
        mock_plex_polling.check_request = AsyncMock(
            return_value=CheckRequestResult(
                request_id=10,
                matched=False,
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
        mock_plex_polling.check_request.assert_called_once_with(10)

    @pytest.mark.asyncio
    async def test_name_only_torrent_match_logs_qbit_finished_evidence(
        self, mock_db, mock_qbit, mock_plex_polling
    ):
        """Name-only approved torrents should persist qBit state/progress evidence."""
        from app.siftarr.models.request import MediaType, RequestStatus
        from app.siftarr.services.admin.plex_polling_service import CheckRequestResult

        torrent = MagicMock()
        torrent.id = 2
        torrent.request_id = 40
        torrent.title = "The Cheetah Girls 2003"
        torrent.magnet_url = None

        request = MagicMock()
        request.id = 40
        request.title = "The Cheetah Girls"
        request.media_type = MediaType.MOVIE
        request.status = RequestStatus.DOWNLOADING

        mock_qbit.get_all_active_torrents = AsyncMock(
            return_value=[
                {"hash": None, "name": torrent.title, "progress": 1.0, "state": "stalledUP"}
            ]
        )
        mock_plex_polling.check_request = AsyncMock(
            return_value=CheckRequestResult(
                request_id=40,
                matched=False,
                available=False,
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

        assert result == 0
        added_log = mock_db.add.call_args.args[0]
        details = json.loads(added_log.details)
        assert details["done_torrents"] == [
            {
                "torrent_id": 2,
                "title": "The Cheetah Girls 2003",
                "hash": None,
                "qbit_found": True,
                "qbit_progress": 1.0,
                "qbit_state": "stalledUP",
            }
        ]

    @pytest.mark.asyncio
    async def test_qbit_evidence_overseerr_sync_failure_does_not_block(
        self, mock_db, mock_qbit, mock_plex_polling, monkeypatch
    ):
        """Failed Overseerr approval sync should not block qBit/Plex reconciliation."""
        from app.siftarr.models.request import MediaType, RequestStatus
        from app.siftarr.services.admin.plex_polling_service import CheckRequestResult
        from app.siftarr.services.lifecycle import download_completion_service as module

        torrent = MagicMock()
        torrent.id = 3
        torrent.request_id = 41
        torrent.title = "Test Movie 2024"
        torrent.magnet_url = "magnet:?xt=urn:btih:da39a3ee5e6b4b0d3255bfef95601890afd80709"

        request = MagicMock()
        request.id = 41
        request.title = "Test Movie"
        request.media_type = MediaType.MOVIE
        request.status = RequestStatus.DOWNLOADING
        request.overseerr_request_id = 410

        mock_qbit.get_all_active_torrents = AsyncMock(
            return_value=[
                {
                    "hash": "da39a3ee5e6b4b0d3255bfef95601890afd80709",
                    "name": torrent.title,
                    "progress": 0.5,
                    "state": "downloading",
                }
            ]
        )
        mock_plex_polling.check_request = AsyncMock(
            return_value=CheckRequestResult(
                request_id=41,
                available=False,
                status_before=RequestStatus.DOWNLOADING,
                status_after=RequestStatus.DOWNLOADING,
            )
        )
        mock_db.execute.return_value = _rows_result([(torrent, request)])
        sync = AsyncMock(return_value=False)
        monkeypatch.setattr(module, "approve_overseerr_request_best_effort", sync)

        service = DownloadCompletionService(mock_db, mock_qbit, mock_plex_polling)
        result = await service.check_downloading_requests()

        assert result == 0
        sync.assert_awaited_once_with(mock_db, request, reason="qbit_present_evidence")
        mock_plex_polling.check_request.assert_not_called()

    @pytest.mark.asyncio
    async def test_plex_confirms_completion(self, mock_db, mock_qbit, mock_plex_polling):
        """When Plex confirms, request is completed through Plex polling."""
        from app.siftarr.models.request import MediaType, RequestStatus
        from app.siftarr.services.admin.plex_polling_service import CheckRequestResult

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

        mock_plex_polling.check_request = AsyncMock(
            return_value=CheckRequestResult(
                request_id=10,
                matched=True,
                available=True,
                status_before=RequestStatus.DOWNLOADING,
                status_after=RequestStatus.COMPLETED,
                reason="Found on Plex",
            )
        )
        mock_qbit.get_all_active_torrents = AsyncMock(return_value=[])

        mock_db.execute.side_effect = [
            _rows_result([(torrent, request)]),
            _request_id_rows([]),
        ]

        service = DownloadCompletionService(mock_db, mock_qbit, mock_plex_polling)
        result = await service.check_downloading_requests()

        assert result == 1
        mock_plex_polling.check_request.assert_called_once_with(10)

    @pytest.mark.asyncio
    async def test_tv_completion_uses_reconcile_path_for_completed_show(
        self, mock_db, mock_qbit, mock_plex_polling
    ):
        """TV download completion should reuse the Plex reconciliation path, not force completed."""
        from app.siftarr.models.request import MediaType, RequestStatus
        from app.siftarr.services.admin.plex_polling_service import CheckRequestResult

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

        mock_plex_polling.check_request = AsyncMock(
            return_value=CheckRequestResult(
                request_id=10,
                matched=True,
                available=True,
                status_before=RequestStatus.DOWNLOADING,
                status_after=RequestStatus.COMPLETED,
                reason="All episodes found on Plex",
            )
        )
        mock_qbit.get_all_active_torrents = AsyncMock(return_value=[])

        mock_db.execute.side_effect = [
            _rows_result([(torrent, request)]),
            _request_id_rows([]),
        ]

        service = DownloadCompletionService(mock_db, mock_qbit, mock_plex_polling)
        result = await service.check_downloading_requests()

        assert result == 1
        mock_plex_polling.check_request.assert_called_once_with(10)

    @pytest.mark.asyncio
    async def test_logs_plex_reconcile_reason_for_completed_request(
        self, mock_db, mock_qbit, mock_plex_polling, caplog
    ):
        """Completion logging should explain the Plex reconciliation outcome."""
        from app.siftarr.models.request import MediaType, RequestStatus
        from app.siftarr.services.admin.plex_polling_service import CheckRequestResult

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

        mock_plex_polling.check_request = AsyncMock(
            return_value=CheckRequestResult(
                request_id=10,
                matched=True,
                available=True,
                status_before=RequestStatus.DOWNLOADING,
                status_after=RequestStatus.COMPLETED,
                reason="All episodes found on Plex",
            )
        )
        mock_qbit.get_all_active_torrents = AsyncMock(return_value=[])

        mock_db.execute.side_effect = [
            _rows_result([(torrent, request)]),
            _request_id_rows([]),
        ]

        service = DownloadCompletionService(mock_db, mock_qbit, mock_plex_polling)

        with caplog.at_level(logging.INFO):
            result = await service.check_downloading_requests()

        assert result == 1
        assert (
            "DownloadCompletionService: checked request_id=10 title=Test Show via Plex "
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
        mock_qbit.get_all_active_torrents.assert_not_called()
        mock_plex_polling.check_request.assert_not_called()

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

        mock_qbit.get_all_active_torrents = AsyncMock(
            return_value=[
                {
                    "hash": "da39a3ee5e6b4b0d3255bfef95601890afd80709",
                    "name": "Test Show S01E01",
                    "progress": 0.4,
                    "state": "downloading",
                }
            ]
        )
        mock_db.execute.side_effect = [_rows_result([(torrent, request)])]

        service = DownloadCompletionService(mock_db, mock_qbit, mock_plex_polling)
        result = await service.check_downloading_requests()

        assert result == 0
        mock_plex_polling.check_request.assert_not_called()

    @pytest.mark.asyncio
    async def test_tv_request_reconciles_when_any_approved_torrent_finishes(
        self, mock_db, mock_qbit, mock_plex_polling
    ):
        """A completed TV episode should trigger Plex reconciliation even if siblings still download."""
        from app.siftarr.models.request import MediaType, RequestStatus
        from app.siftarr.services.admin.plex_polling_service import CheckRequestResult

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

        mock_qbit.get_all_active_torrents = AsyncMock(
            return_value=[
                {
                    "hash": "da39a3ee5e6b4b0d3255bfef95601890afd80709",
                    "name": "Test Show S01E01",
                    "progress": 1.0,
                    "state": "uploading",
                },
                {
                    "hash": "ea39a3ee5e6b4b0d3255bfef95601890afd80709",
                    "name": "Test Show S01E02",
                    "progress": 0.5,
                    "state": "downloading",
                },
            ]
        )
        mock_plex_polling.check_request = AsyncMock(
            return_value=CheckRequestResult(
                request_id=10,
                matched=True,
                available=True,
                status_before=RequestStatus.DOWNLOADING,
                status_after=RequestStatus.COMPLETED,
                reason="Episode found on Plex",
            )
        )
        mock_db.execute.side_effect = [
            _rows_result([(completed_torrent, request), (downloading_torrent, request)]),
            _request_id_rows([]),
        ]

        service = DownloadCompletionService(mock_db, mock_qbit, mock_plex_polling)
        result = await service.check_downloading_requests()

        assert result == 1
        mock_plex_polling.check_request.assert_called_once_with(10)

    @pytest.mark.asyncio
    async def test_tv_sibling_season_pack_stays_active_after_partial_plex_reconcile(
        self, mock_db, mock_qbit, mock_plex_polling
    ):
        """S01 qBit completion must not complete/hide still-downloading S02."""
        from app.siftarr.models.request import MediaType, RequestStatus
        from app.siftarr.services.admin.plex_polling_service import CheckRequestResult

        season_one = MagicMock()
        season_one.id = 11
        season_one.request_id = 10
        season_one.title = "Test Show S01 1080p"
        season_one.magnet_url = "magnet:?xt=urn:btih:1111111111111111111111111111111111111111"

        season_two = MagicMock()
        season_two.id = 12
        season_two.request_id = 10
        season_two.title = "Test Show S02 1080p"
        season_two.magnet_url = "magnet:?xt=urn:btih:2222222222222222222222222222222222222222"

        request = MagicMock()
        request.id = 10
        request.title = "Test Show"
        request.media_type = MediaType.TV
        request.status = RequestStatus.DOWNLOADING

        mock_qbit.get_all_active_torrents = AsyncMock(
            return_value=[
                {
                    "hash": "1111111111111111111111111111111111111111",
                    "name": season_one.title,
                    "progress": 1.0,
                    "state": "uploading",
                },
                {
                    "hash": "2222222222222222222222222222222222222222",
                    "name": season_two.title,
                    "progress": 0.42,
                    "state": "downloading",
                },
            ]
        )
        mock_plex_polling.check_request = AsyncMock(
            return_value=CheckRequestResult(
                request_id=10,
                matched=True,
                available=True,
                status_before=RequestStatus.DOWNLOADING,
                status_after=RequestStatus.DOWNLOADING,
                reason="Some episodes found on Plex",
            )
        )
        mock_db.execute.side_effect = [
            _rows_result([(season_one, request), (season_two, request)]),
            _request_id_rows([]),
        ]

        service = DownloadCompletionService(mock_db, mock_qbit, mock_plex_polling)
        result = await service.check_downloading_requests()

        assert result == 0
        assert request.status == RequestStatus.DOWNLOADING
        details = json.loads(mock_db.add.call_args.args[0].details)
        assert [item["torrent_id"] for item in details["done_torrents"]] == [11]
        assert details["done_torrents"][0]["qbit_progress"] == 1.0
        mock_plex_polling.check_request.assert_called_once_with(10)

    @pytest.mark.asyncio
    async def test_download_completed_log_is_deduplicated_per_torrent(
        self, mock_db, mock_qbit, mock_plex_polling
    ):
        """Existing download_completed activity for the same torrent should not be logged again."""
        from app.siftarr.models.request import MediaType, RequestStatus
        from app.siftarr.services.admin.plex_polling_service import CheckRequestResult

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

        mock_qbit.get_all_active_torrents = AsyncMock(
            return_value=[
                {
                    "hash": "da39a3ee5e6b4b0d3255bfef95601890afd80709",
                    "name": "Test Show S01E01",
                    "progress": 1.0,
                    "state": "uploading",
                }
            ]
        )
        mock_plex_polling.check_request = AsyncMock(
            return_value=CheckRequestResult(
                request_id=10,
                matched=False,
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

    @pytest.mark.asyncio
    async def test_tv_episode_download_completed_logs_are_per_torrent(
        self, mock_db, mock_qbit, mock_plex_polling
    ):
        """Two approved TV episode torrents on one request should log completion independently."""
        from app.siftarr.models.request import MediaType, RequestStatus
        from app.siftarr.services.admin.plex_polling_service import CheckRequestResult

        first = MagicMock()
        first.id = 1
        first.request_id = 10
        first.title = "Test Show S01E01"
        first.magnet_url = "magnet:?xt=urn:btih:da39a3ee5e6b4b0d3255bfef95601890afd80709"

        second = MagicMock()
        second.id = 2
        second.request_id = 10
        second.title = "Test Show S01E02"
        second.magnet_url = "magnet:?xt=urn:btih:ea39a3ee5e6b4b0d3255bfef95601890afd80709"

        request = MagicMock()
        request.id = 10
        request.title = "Test Show"
        request.media_type = MediaType.TV
        request.status = RequestStatus.DOWNLOADING

        mock_qbit.get_all_active_torrents = AsyncMock(
            return_value=[
                {
                    "hash": "da39a3ee5e6b4b0d3255bfef95601890afd80709",
                    "name": first.title,
                    "progress": 1.0,
                    "state": "uploading",
                },
                {
                    "hash": "ea39a3ee5e6b4b0d3255bfef95601890afd80709",
                    "name": second.title,
                    "progress": 1.0,
                    "state": "uploading",
                },
            ]
        )
        mock_plex_polling.check_request = AsyncMock(
            return_value=CheckRequestResult(
                request_id=10,
                matched=False,
                available=False,
                status_before=RequestStatus.DOWNLOADING,
                status_after=RequestStatus.DOWNLOADING,
            )
        )
        mock_db.execute.side_effect = [
            _rows_result([(first, request), (second, request)]),
            _completion_log_rows(
                [(10, {"done_torrents": [{"torrent_id": 1, "title": first.title}]})]
            ),
        ]

        service = DownloadCompletionService(mock_db, mock_qbit, mock_plex_polling)
        result = await service.check_downloading_requests()

        assert result == 0
        details = json.loads(mock_db.add.call_args.args[0].details)
        assert [item["torrent_id"] for item in details["done_torrents"]] == [2]
        mock_plex_polling.check_request.assert_called_once_with(10)

    @pytest.mark.asyncio
    async def test_qbit_progress_one_retries_plex_without_completing_request(
        self, mock_db, mock_qbit, mock_plex_polling
    ):
        """qBit progress 1.0 should log once and leave request downloading until Plex confirms."""
        from app.siftarr.models.request import MediaType, RequestStatus
        from app.siftarr.services.admin.plex_polling_service import CheckRequestResult

        torrent = MagicMock()
        torrent.id = 1
        torrent.request_id = 10
        torrent.title = "Test Movie 2020"
        torrent.magnet_url = "magnet:?xt=urn:btih:da39a3ee5e6b4b0d3255bfef95601890afd80709"

        request = MagicMock()
        request.id = 10
        request.title = "Test Movie"
        request.media_type = MediaType.MOVIE
        request.status = RequestStatus.DOWNLOADING

        mock_qbit.get_all_active_torrents = AsyncMock(
            return_value=[
                {
                    "hash": "da39a3ee5e6b4b0d3255bfef95601890afd80709",
                    "name": "Test Movie 2020",
                    "progress": 1.0,
                    "state": "stalledUP",
                }
            ]
        )
        mock_plex_polling.check_request = AsyncMock(
            return_value=CheckRequestResult(
                request_id=10,
                matched=False,
                available=False,
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

        assert result == 0
        assert request.status == RequestStatus.DOWNLOADING
        mock_plex_polling.check_request.assert_called_once_with(10)
        details = json.loads(mock_db.add.call_args.args[0].details)
        assert details["done_torrents"][0]["qbit_progress"] == 1.0
        assert details["done_torrents"][0]["qbit_state"] == "stalledUP"
