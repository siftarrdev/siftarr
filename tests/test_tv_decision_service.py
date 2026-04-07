"""Tests for TVDecisionService."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.siftarr.models.request import MediaType, Request, RequestStatus
from app.siftarr.services.tv_decision_service import TVDecisionService


class TestTVDecisionService:
    @pytest.fixture
    def mock_db(self):
        return AsyncMock()

    @pytest.fixture
    def service(self, mock_db):
        prowlarr = AsyncMock()
        qbittorrent = AsyncMock()
        return TVDecisionService(mock_db, prowlarr, qbittorrent)

    def test_get_requested_episodes_handles_list_format(self, service):
        request = MagicMock(spec=Request)
        request.requested_episodes = "[14, 15]"
        request.requested_seasons = "[8]"

        assert service._get_requested_episodes(request) == {8: [14, 15]}

    @pytest.mark.asyncio
    async def test_process_request_logs_and_uses_episode_search(
        self, mock_db, service, monkeypatch
    ):
        request = MagicMock(spec=Request)
        request.id = 1
        request.media_type = MediaType.TV
        request.tvdb_id = 123
        request.title = "The Rookie"
        request.year = 2024
        request.status = RequestStatus.PENDING
        request.requested_seasons = "[8]"
        request.requested_episodes = "[14]"

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = request
        mock_db.execute.return_value = mock_result

        season_search_result = MagicMock()
        season_search_result.releases = []
        episode_search_result = MagicMock()
        episode_search_result.releases = []
        service.prowlarr.search_by_tvdbid = AsyncMock(
            side_effect=[season_search_result, episode_search_result]
        )

        monkeypatch.setattr(service, "_get_rule_engine", AsyncMock(return_value=MagicMock()))
        monkeypatch.setattr(
            "app.siftarr.services.tv_decision_service.store_search_results",
            AsyncMock(),
        )
        pending_queue = MagicMock()
        pending_queue.add_to_queue = AsyncMock()
        monkeypatch.setattr(
            "app.siftarr.services.tv_decision_service.PendingQueueService",
            lambda db: pending_queue,
        )

        result = await service.process_request(1)

        assert result["status"] == "pending"
        assert service.prowlarr.search_by_tvdbid.await_count == 2
