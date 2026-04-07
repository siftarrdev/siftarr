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

        response = await staged.approve_staged_torrent(1, db=mock_db)

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

        response = await staged.approve_staged_torrent(3, db=mock_db)

        assert response.status_code == 303
        log_decision.assert_called_once_with(
            request=request,
            approved_torrent=torrent,
            rules_selected_torrent=rule_torrent,
        )
