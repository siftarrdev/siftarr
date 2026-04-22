"""Tests for staged torrent approval routes."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.siftarr.models.request import MediaType, RequestStatus
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

        monkeypatch.setattr(staged, "get_settings", lambda: MagicMock())
        monkeypatch.setattr(staged, "QbittorrentService", MagicMock(return_value=qbittorrent))
        monkeypatch.setattr(staged, "LifecycleService", MagicMock(return_value=lifecycle_service))
        monkeypatch.setattr(staged, "log_staging_decision", log_decision)
        monkeypatch.setattr(staged.os.path, "exists", MagicMock(return_value=False))

        response = await staged.approve_staged_torrent(
            1, http_request=MagicMock(headers={}), db=mock_db
        )

        assert response.status_code == 303
        assert torrent.status == "approved"
        lifecycle_service.transition.assert_awaited_once_with(request.id, RequestStatus.DOWNLOADING)
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

        monkeypatch.setattr(staged, "get_settings", lambda: MagicMock())
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
        monkeypatch.setattr(staged, "get_settings", lambda: MagicMock())
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
        assert lifecycle_service.transition.await_count == 2

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

    @pytest.mark.asyncio
    async def test_replace_staged_torrent_redirects_to_staged_tab(self, mock_db, monkeypatch):
        """Replacing a downloading torrent should keep the user on the staged tab."""
        new_torrent = MagicMock()
        new_torrent.id = 9
        new_torrent.request_id = 4
        new_torrent.magnet_url = "magnet:?xt=urn:btih:def"
        new_torrent.torrent_path = "/tmp/new.torrent"
        new_torrent.json_path = "/tmp/new.json"
        new_torrent.status = "staged"

        old_torrent = MagicMock()
        old_torrent.id = 10
        old_torrent.request_id = 4
        old_torrent.status = "approved"

        request = MagicMock()
        request.id = 4
        request.media_type = MediaType.TV

        new_result = MagicMock()
        new_result.scalar_one_or_none.return_value = new_torrent
        request_result = MagicMock()
        request_result.scalar_one_or_none.return_value = request
        old_result = MagicMock()
        old_result.scalar_one_or_none.return_value = old_torrent
        mock_db.execute.side_effect = [new_result, request_result, old_result]

        qbittorrent = AsyncMock()
        qbittorrent.add_torrent.return_value = "hash456"
        monkeypatch.setattr(staged, "get_settings", lambda: MagicMock())
        monkeypatch.setattr(staged, "QbittorrentService", MagicMock(return_value=qbittorrent))
        monkeypatch.setattr(staged, "log_replacement_decision", MagicMock())
        monkeypatch.setattr(staged.os.path, "exists", MagicMock(return_value=False))

        response = await staged.replace_staged_torrent(
            torrent_id=9, reason="Better quality", db=mock_db
        )

        assert response.status_code == 303
        assert response.headers["location"] == "/?tab=staged"
        assert old_torrent.status == "replaced"
        assert new_torrent.status == "approved"


class TestDownloadStatusEndpoint:
    """Tests for GET /staged/download-status."""

    @pytest.fixture
    def mock_db(self):
        db = AsyncMock()
        db.add = MagicMock()
        db.flush = AsyncMock()
        db.commit = AsyncMock()
        return db

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
        monkeypatch.setattr(staged, "get_settings", lambda: MagicMock())
        monkeypatch.setattr(staged_module, "QbittorrentService", MagicMock(return_value=qbit))

        response = await get_download_status(db=mock_db)
        assert response.status_code == 200
        body = json.loads(bytes(response.body))  # type: ignore[arg-type]
        assert len(body["torrents"]) == 1
        assert body["torrents"][0]["id"] == 5
        assert body["torrents"][0]["qbit_progress"] == 0.6
        assert body["torrents"][0]["refresh_staged_tab"] is False

    @pytest.mark.asyncio
    async def test_ignores_resolved_request_torrents(self, mock_db, monkeypatch):
        """Approved torrents for available or partial requests should not poll as active."""
        import json

        from app.siftarr.routers import staged as staged_module
        from app.siftarr.routers.staged import get_download_status

        active_torrent = MagicMock()
        active_torrent.id = 5
        active_torrent.title = "Still Downloading"
        active_torrent.request_id = 99
        active_torrent.magnet_url = "magnet:?xt=urn:btih:da39a3ee5e6b4b0d3255bfef95601890afd80709"
        active_torrent.status = "approved"

        available_torrent = MagicMock()
        available_torrent.id = 6
        available_torrent.title = "Already Available"
        available_torrent.request_id = 100
        available_torrent.magnet_url = (
            "magnet:?xt=urn:btih:ea39a3ee5e6b4b0d3255bfef95601890afd80709"
        )
        available_torrent.status = "approved"

        partial_torrent = MagicMock()
        partial_torrent.id = 7
        partial_torrent.title = "Partially Available"
        partial_torrent.request_id = 101
        partial_torrent.magnet_url = "magnet:?xt=urn:btih:fa39a3ee5e6b4b0d3255bfef95601890afd80709"
        partial_torrent.status = "approved"

        torrent_result = MagicMock()
        torrent_result.scalars.return_value.all.return_value = [
            active_torrent,
            available_torrent,
            partial_torrent,
        ]

        request_status_result = MagicMock()
        request_status_result.all.return_value = [
            (99, RequestStatus.DOWNLOADING),
            (100, RequestStatus.COMPLETED),
            (101, RequestStatus.COMPLETED),
        ]

        mock_db.execute.side_effect = [torrent_result, request_status_result]

        qbit = AsyncMock()
        qbit.get_torrent_info = AsyncMock(return_value={"progress": 0.6, "state": "downloading"})
        monkeypatch.setattr(staged, "get_settings", lambda: MagicMock())
        monkeypatch.setattr(staged_module, "QbittorrentService", MagicMock(return_value=qbit))

        response = await get_download_status(db=mock_db)
        assert response.status_code == 200
        body = json.loads(bytes(response.body))  # type: ignore[arg-type]
        assert [torrent["id"] for torrent in body["torrents"]] == [5]
        qbit.get_torrent_info.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_download_status_does_not_reconcile_on_get(self, mock_db, monkeypatch):
        """GET download-status should not perform Plex check side effects."""
        import json

        from app.siftarr.routers import staged as staged_module
        from app.siftarr.routers.staged import get_download_status

        torrent = MagicMock()
        torrent.id = 5
        torrent.title = "Test Show S01E01"
        torrent.request_id = 99
        torrent.magnet_url = "magnet:?xt=urn:btih:da39a3ee5e6b4b0d3255bfef95601890afd80709"
        torrent.status = "approved"

        torrent_result = MagicMock()
        torrent_result.scalars.return_value.all.return_value = [torrent]

        request_status_result = MagicMock()
        request_status_result.all.return_value = [(99, RequestStatus.DOWNLOADING)]

        mock_db.execute.side_effect = [torrent_result, request_status_result]

        qbit = AsyncMock()
        qbit.get_torrent_info = AsyncMock(return_value={"progress": 1.0, "state": "uploading"})

        monkeypatch.setattr(staged, "get_settings", lambda: MagicMock())
        monkeypatch.setattr(staged_module, "QbittorrentService", MagicMock(return_value=qbit))

        response = await get_download_status(db=mock_db)

        body = json.loads(bytes(response.body))  # type: ignore[arg-type]
        assert body["torrents"][0]["qbit_complete"] is True
        assert body["torrents"][0]["plex_available"] is False
        assert body["torrents"][0]["request_status"] == RequestStatus.DOWNLOADING.value
        assert body["torrents"][0]["refresh_staged_tab"] is True

    @pytest.mark.asyncio
    async def test_reconcile_request_via_plex_closes_service_on_error(self, mock_db, monkeypatch):
        """Targeted check should always close PlexService."""
        from app.siftarr.routers.staged import _reconcile_request_via_plex

        runtime_settings = MagicMock()
        plex_service = AsyncMock()
        plex_polling = AsyncMock()
        plex_polling.check_request = AsyncMock(side_effect=RuntimeError("plex boom"))

        monkeypatch.setattr(staged, "PlexService", MagicMock(return_value=plex_service))
        monkeypatch.setattr(staged, "PlexPollingService", MagicMock(return_value=plex_polling))

        with pytest.raises(RuntimeError, match="plex boom"):
            await _reconcile_request_via_plex(
                mock_db,
                request_id=99,
                title="Test Show S01E01",
                runtime_settings=runtime_settings,
            )

        plex_service.close.assert_awaited_once()


class TestCheckNowEndpoint:
    @pytest.fixture
    def mock_db(self):
        db = AsyncMock()
        db.add = MagicMock()
        db.flush = AsyncMock()
        db.commit = AsyncMock()
        return db

    @pytest.mark.asyncio
    async def test_check_now_does_not_reconcile_incomplete_torrent(self, mock_db, monkeypatch):
        """Incomplete check-now requests should not trigger Plex checks."""
        import json

        from app.siftarr.routers import staged as staged_module
        from app.siftarr.routers.staged import check_now

        torrent = MagicMock()
        torrent.id = 7
        torrent.title = "Test Show S01E01"
        torrent.request_id = 77
        torrent.magnet_url = "magnet:?xt=urn:btih:da39a3ee5e6b4b0d3255bfef95601890afd80709"

        torrent_result = MagicMock()
        torrent_result.scalar_one_or_none.return_value = torrent
        mock_db.execute = AsyncMock(return_value=torrent_result)
        mock_db.commit = AsyncMock()

        qbit = AsyncMock()
        qbit.get_torrent_info = AsyncMock(return_value={"progress": 0.2, "state": "downloading"})
        plex_polling = AsyncMock()

        monkeypatch.setattr(staged, "get_settings", lambda: MagicMock())
        monkeypatch.setattr(staged_module, "QbittorrentService", MagicMock(return_value=qbit))
        monkeypatch.setattr(staged_module, "PlexService", MagicMock(return_value=AsyncMock()))
        monkeypatch.setattr(
            staged_module, "PlexPollingService", MagicMock(return_value=plex_polling)
        )

        response = await check_now(torrent_id=7, db=mock_db)

        body = json.loads(bytes(response.body))  # type: ignore[arg-type]
        assert body["qbit_complete"] is False
        assert body["plex_available"] is False
        plex_polling.check_request.assert_not_called()
