"""Tests for timeline data in the request details API endpoint."""

import json
import sys
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

if sys.version_info < (3, 11):  # noqa: UP036
    pytest.skip("Requires Python 3.11+ for StrEnum", allow_module_level=True)

from app.siftarr.models.activity_log import ActivityLog  # noqa: E402
from app.siftarr.services import dashboard_service  # noqa: E402


def _make_log_entry(
    id: int,
    event_type: str,
    request_id: int,
    details: dict | None = None,
    created_at: datetime | None = None,
) -> MagicMock:
    entry = MagicMock(spec=ActivityLog)
    entry.id = id
    entry.event_type = event_type
    entry.request_id = request_id
    entry.details = json.dumps(details) if details else None
    entry.created_at = created_at or datetime(2026, 4, 20, 12, 0, 0, tzinfo=UTC)
    return entry


def _make_request(
    id: int = 1, title: str = "Test Movie", status: str = "searching", media_type: str = "movie"
):
    req = MagicMock()
    req.id = id
    req.title = title
    req.status = MagicMock(value=status)
    req.media_type = MagicMock(value=media_type)
    req.overseerr_request_id = None
    req.tmdb_id = None
    req.year = 2026
    return req


@pytest.fixture
def mock_db():
    db = AsyncMock()
    return db


class TestTimelineInDetails:
    """Test timeline array in the request_details endpoint response."""

    @pytest.mark.asyncio
    @patch("app.siftarr.config.get_settings")
    @patch("app.siftarr.routers.dashboard_api.load_request_or_404", new_callable=AsyncMock)
    async def test_details_returns_timeline_array(self, mock_load, mock_settings, mock_db):
        """The details endpoint includes a timeline array in its response."""
        from fastapi import BackgroundTasks

        from app.siftarr.routers.dashboard_api import request_details

        request = _make_request()
        mock_load.return_value = request
        mock_settings.return_value = MagicMock(overseerr_url="http://localhost")

        log1 = _make_log_entry(
            1, "search_started", 1, created_at=datetime(2026, 4, 20, 10, 0, tzinfo=UTC)
        )
        log2 = _make_log_entry(
            2,
            "search_completed",
            1,
            details={"result_count": 5},
            created_at=datetime(2026, 4, 20, 10, 1, tzinfo=UTC),
        )

        # Mock db.execute for releases query (returns empty) and rules query (returns empty)
        release_result = MagicMock()
        release_result.scalars.return_value.all.return_value = []
        rules_result = MagicMock()
        rules_result.scalars.return_value.all.return_value = []
        staged_result = MagicMock()
        staged_result.scalars.return_value.first.return_value = None

        mock_db.execute = AsyncMock(side_effect=[release_result, rules_result, staged_result])

        # Mock ActivityLogService.get_timeline to return entries newest-first (as the real service does)
        with patch.object(dashboard_service, "ActivityLogService") as MockService:
            instance = MockService.return_value
            instance.get_timeline = AsyncMock(return_value=[log2, log1])

            bg = BackgroundTasks()
            resp = await request_details(request_id=1, background_tasks=bg, db=mock_db)

        body = json.loads(bytes(resp.body))
        assert "timeline" in body
        assert isinstance(body["timeline"], list)
        assert len(body["timeline"]) == 2

    @pytest.mark.asyncio
    @patch("app.siftarr.config.get_settings")
    @patch("app.siftarr.routers.dashboard_api.load_request_or_404", new_callable=AsyncMock)
    async def test_timeline_ordered_chronologically(self, mock_load, mock_settings, mock_db):
        """Timeline entries are returned oldest-first (chronological order)."""
        from fastapi import BackgroundTasks

        from app.siftarr.routers.dashboard_api import request_details

        request = _make_request()
        mock_load.return_value = request
        mock_settings.return_value = MagicMock(overseerr_url="http://localhost")

        early = _make_log_entry(
            1, "search_started", 1, created_at=datetime(2026, 4, 20, 10, 0, tzinfo=UTC)
        )
        late = _make_log_entry(
            2, "search_completed", 1, created_at=datetime(2026, 4, 20, 11, 0, tzinfo=UTC)
        )

        release_result = MagicMock()
        release_result.scalars.return_value.all.return_value = []
        rules_result = MagicMock()
        rules_result.scalars.return_value.all.return_value = []
        staged_result = MagicMock()
        staged_result.scalars.return_value.first.return_value = None
        mock_db.execute = AsyncMock(side_effect=[release_result, rules_result, staged_result])

        with patch.object(dashboard_service, "ActivityLogService") as MockService:
            instance = MockService.return_value
            # Service returns newest first
            instance.get_timeline = AsyncMock(return_value=[late, early])

            bg = BackgroundTasks()
            resp = await request_details(request_id=1, background_tasks=bg, db=mock_db)

        body = json.loads(bytes(resp.body))
        timeline = body["timeline"]
        assert timeline[0]["id"] == 1  # early entry first
        assert timeline[1]["id"] == 2  # late entry second

    @pytest.mark.asyncio
    @patch("app.siftarr.config.get_settings")
    @patch("app.siftarr.routers.dashboard_api.load_request_or_404", new_callable=AsyncMock)
    async def test_timeline_empty_when_no_logs(self, mock_load, mock_settings, mock_db):
        """Timeline is an empty array when no activity logs exist for the request."""
        from fastapi import BackgroundTasks

        from app.siftarr.routers.dashboard_api import request_details

        request = _make_request()
        mock_load.return_value = request
        mock_settings.return_value = MagicMock(overseerr_url="http://localhost")

        release_result = MagicMock()
        release_result.scalars.return_value.all.return_value = []
        rules_result = MagicMock()
        rules_result.scalars.return_value.all.return_value = []
        staged_result = MagicMock()
        staged_result.scalars.return_value.first.return_value = None
        mock_db.execute = AsyncMock(side_effect=[release_result, rules_result, staged_result])

        with patch.object(dashboard_service, "ActivityLogService") as MockService:
            instance = MockService.return_value
            instance.get_timeline = AsyncMock(return_value=[])

            bg = BackgroundTasks()
            resp = await request_details(request_id=1, background_tasks=bg, db=mock_db)

        body = json.loads(bytes(resp.body))
        assert body["timeline"] == []
