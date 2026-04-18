"""Tests for staged torrent approval routes."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.siftarr.models.request import MediaType
from app.siftarr.routers import staged


class TestStagedRouter:
    """Focused tests for staged approval behavior."""

    @pytest.fixture
    def mock_db(self):
        """Create a mock database session."""
        return AsyncMock()

    @pytest.mark.asyncio
    async def test_approve_staged_torrent_logs_rule_accept(self, mock_db, monkeypatch):
        """Approving the rule-selected torrent should log a rule_accept decision."""
        torrent = MagicMock()
        torrent.id = 1
        torrent.request_id = 2
        torrent.magnet_url = "magnet:?xt=urn:btih:abc"
        torrent.status = "staged"
        torrent.selection_source = "rule"
        torrent.torrent_path = "/tmp/test.torrent"
        torrent.json_path = "/tmp/test.json"

        request = MagicMock()
        request.id = 2
        request.media_type = MediaType.MOVIE

        rule_result = MagicMock()
        rule_result.scalars.return_value.first.return_value = torrent

        torrent_result = MagicMock()
        torrent_result.scalar_one_or_none.return_value = torrent
        request_result = MagicMock()
        request_result.scalar_one_or_none.return_value = request
        mock_db.execute.side_effect = [torrent_result, request_result, rule_result]

        qbittorrent = AsyncMock()
        qbittorrent.add_torrent.return_value = "hash123"
        lifecycle_service = AsyncMock()
        log_decision = MagicMock()

        monkeypatch.setattr(staged, "get_effective_settings", AsyncMock(return_value=MagicMock()))
        monkeypatch.setattr(staged, "QbittorrentService", MagicMock(return_value=qbittorrent))
        monkeypatch.setattr(staged, "LifecycleService", MagicMock(return_value=lifecycle_service))
        monkeypatch.setattr(staged, "log_staging_decision", log_decision)
        monkeypatch.setattr(staged.os.path, "exists", MagicMock(return_value=False))

        response = await staged.approve_staged_torrent(
            1, http_request=MagicMock(headers={}), db=mock_db
        )

        assert response.status_code == 303
        assert torrent.status == "approved"
        lifecycle_service.mark_as_downloading.assert_awaited_once_with(request.id)
        log_decision.assert_called_once_with(
            request=request,
            approved_torrent=torrent,
            rules_selected_torrent=torrent,
        )

    @pytest.mark.asyncio
    async def test_approve_staged_torrent_logs_manual_override(self, mock_db, monkeypatch):
        """Approving a manual torrent should log against the current rule-picked torrent."""
        torrent = MagicMock()
        torrent.id = 3
        torrent.request_id = 4
        torrent.magnet_url = "magnet:?xt=urn:btih:def"
        torrent.status = "staged"
        torrent.selection_source = "manual"
        torrent.torrent_path = "/tmp/test2.torrent"
        torrent.json_path = "/tmp/test2.json"

        request = MagicMock()
        request.id = 4
        request.media_type = MediaType.TV

        rule_torrent = MagicMock()
        rule_torrent.id = 5
        rule_torrent.selection_source = "rule"

        rule_result = MagicMock()
        rule_result.scalars.return_value.first.return_value = rule_torrent

        torrent_result = MagicMock()
        torrent_result.scalar_one_or_none.return_value = torrent
        request_result = MagicMock()
        request_result.scalar_one_or_none.return_value = request
        mock_db.execute.side_effect = [torrent_result, request_result, rule_result]

        qbittorrent = AsyncMock()
        qbittorrent.add_torrent.return_value = "hash456"
        lifecycle_service = AsyncMock()
        log_decision = MagicMock()

        monkeypatch.setattr(staged, "get_effective_settings", AsyncMock(return_value=MagicMock()))
        monkeypatch.setattr(staged, "QbittorrentService", MagicMock(return_value=qbittorrent))
        monkeypatch.setattr(staged, "LifecycleService", MagicMock(return_value=lifecycle_service))
        monkeypatch.setattr(staged, "log_staging_decision", log_decision)
        monkeypatch.setattr(staged.os.path, "exists", MagicMock(return_value=False))

        response = await staged.approve_staged_torrent(
            3, http_request=MagicMock(headers={}), db=mock_db
        )

        assert response.status_code == 303
        log_decision.assert_called_once_with(
            request=request,
            approved_torrent=torrent,
            rules_selected_torrent=rule_torrent,
        )

    @pytest.mark.asyncio
    async def test_bulk_staged_action_approves_selected(self, mock_db, monkeypatch):
        """Bulk approve should process multiple staged torrents."""
        torrent_one = MagicMock()
        torrent_one.id = 1
        torrent_one.request_id = 10
        torrent_one.magnet_url = "magnet:?xt=urn:btih:abc"
        torrent_one.status = "staged"
        torrent_one.selection_source = "rule"
        torrent_one.torrent_path = "/tmp/one.torrent"
        torrent_one.json_path = "/tmp/one.json"

        torrent_two = MagicMock()
        torrent_two.id = 2
        torrent_two.request_id = 11
        torrent_two.magnet_url = "magnet:?xt=urn:btih:def"
        torrent_two.status = "staged"
        torrent_two.selection_source = "rule"
        torrent_two.torrent_path = "/tmp/two.torrent"
        torrent_two.json_path = "/tmp/two.json"

        torrent_result = MagicMock()
        torrent_result.scalars.return_value.all.return_value = [torrent_one, torrent_two]
        mock_db.execute.return_value = torrent_result

        qbittorrent = AsyncMock()
        qbittorrent.add_torrent.side_effect = ["hash1", "hash2"]
        lifecycle_service = AsyncMock()
        monkeypatch.setattr(staged, "get_effective_settings", AsyncMock(return_value=MagicMock()))
        monkeypatch.setattr(staged, "QbittorrentService", MagicMock(return_value=qbittorrent))
        monkeypatch.setattr(staged, "LifecycleService", MagicMock(return_value=lifecycle_service))
        monkeypatch.setattr(staged, "log_staging_decision", MagicMock())
        monkeypatch.setattr(staged.os.path, "exists", MagicMock(return_value=False))

        response = await staged.bulk_staged_action(
            action="approve",
            torrent_ids=[1, 2],
            http_request=MagicMock(headers={"accept": "application/json"}),
            db=mock_db,
        )

        assert response.status_code == 200
        assert torrent_one.status == "approved"
        assert torrent_two.status == "approved"
        assert lifecycle_service.mark_as_downloading.await_count == 2

    @pytest.mark.asyncio
    async def test_bulk_staged_action_discards_selected(self, mock_db, monkeypatch):
        """Bulk discard should process multiple staged torrents."""
        torrent_one = MagicMock()
        torrent_one.id = 3
        torrent_one.request_id = None
        torrent_one.status = "staged"
        torrent_one.torrent_path = "/tmp/three.torrent"
        torrent_one.json_path = "/tmp/three.json"

        torrent_two = MagicMock()
        torrent_two.id = 4
        torrent_two.request_id = None
        torrent_two.status = "staged"
        torrent_two.torrent_path = "/tmp/four.torrent"
        torrent_two.json_path = "/tmp/four.json"

        torrent_result = MagicMock()
        torrent_result.scalars.return_value.all.return_value = [torrent_one, torrent_two]
        mock_db.execute.return_value = torrent_result

        monkeypatch.setattr(staged.os.path, "exists", MagicMock(return_value=False))

        response = await staged.bulk_staged_action(
            action="discard",
            torrent_ids=[3, 4],
            http_request=MagicMock(headers={"accept": "application/json"}),
            db=mock_db,
        )

        assert response.status_code == 200
        assert torrent_one.status == "discarded"
        assert torrent_two.status == "discarded"


class TestDownloadStatusEndpoint:
    """Tests for GET /staged/download-status."""

    @pytest.fixture
    def mock_db(self):
        return AsyncMock()

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_approved_torrents(self, mock_db, monkeypatch):
        """Returns empty list when no approved torrents."""
        from app.siftarr.routers.staged import get_download_status

        empty_result = MagicMock()
        empty_result.scalars.return_value.all.return_value = []
        mock_db.execute.return_value = empty_result

        response = await get_download_status(db=mock_db)
        assert response.status_code == 200
        import json

        body = json.loads(bytes(response.body))  # type: ignore[arg-type]
        assert body == {"torrents": []}

    @pytest.mark.asyncio
    async def test_returns_torrent_status(self, mock_db, monkeypatch):
        """Returns torrent list with qbit progress."""
        import json

        from app.siftarr.models.request import RequestStatus
        from app.siftarr.routers import staged as staged_module
        from app.siftarr.routers.staged import get_download_status

        torrent = MagicMock()
        torrent.id = 5
        torrent.title = "Test Movie"
        torrent.request_id = 99
        torrent.magnet_url = "magnet:?xt=urn:btih:da39a3ee5e6b4b0d3255bfef95601890afd80709"
        torrent.status = "approved"

        torrent_result = MagicMock()
        torrent_result.scalars.return_value.all.return_value = [torrent]

        request_status_result = MagicMock()
        request_status_result.all.return_value = [(99, RequestStatus.DOWNLOADING)]

        mock_db.execute.side_effect = [torrent_result, request_status_result]

        qbit = AsyncMock()
        qbit.get_torrent_info = AsyncMock(return_value={"progress": 0.6, "state": "downloading"})
        monkeypatch.setattr(
            staged_module, "get_effective_settings", AsyncMock(return_value=MagicMock())
        )
        monkeypatch.setattr(staged_module, "QbittorrentService", MagicMock(return_value=qbit))

        response = await get_download_status(db=mock_db)
        assert response.status_code == 200
        body = json.loads(bytes(response.body))  # type: ignore[arg-type]
        assert len(body["torrents"]) == 1
        assert body["torrents"][0]["id"] == 5
        assert body["torrents"][0]["qbit_progress"] == 0.6
