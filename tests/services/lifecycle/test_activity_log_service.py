"""Tests for ActivityLogService."""

import json
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# StrEnum requires Python 3.11+; skip on older interpreters.
if sys.version_info < (3, 11):  # noqa: UP036
    pytest.skip("Requires Python 3.11+ for StrEnum", allow_module_level=True)

from app.siftarr.models import ActivityLog, EventType, StatsTimingEvent  # noqa: E402
from app.siftarr.services.lifecycle.activity_log_service import ActivityLogService  # noqa: E402


class TestActivityLogService:
    """Test cases for ActivityLogService."""

    @pytest.fixture
    def mock_db(self):
        """Create a mock database session."""
        db = AsyncMock()
        db.add = MagicMock()

        # Python 3.14: AsyncMock.__call__ returns a coroutine, not an
        # AsyncMock instance, so "async with mock.begin_nested():" would
        # fail.  Replace with a MagicMock that returns a proper async
        # context manager.
        nested_context = AsyncMock()
        nested_context.__aenter__.return_value = None
        nested_context.__aexit__.return_value = None
        db.begin_nested = MagicMock(return_value=nested_context)

        return db

    @pytest.fixture
    def service(self, mock_db):
        return ActivityLogService(mock_db)

    @pytest.mark.asyncio
    async def test_log_creates_row_with_correct_fields(self, mock_db, service):
        """log() creates an ActivityLog with correct event_type, request_id, details."""
        result = await service.log(
            EventType.SEARCH_STARTED,
            request_id=42,
            details={"query": "test"},
        )

        assert mock_db.add.call_count == 2
        added: ActivityLog = mock_db.add.call_args_list[0][0][0]
        assert isinstance(added, ActivityLog)
        assert added.event_type == "search_started"
        assert added.request_id == 42
        assert json.loads(added.details) == {"query": "test"}  # type: ignore[arg-type]
        timing: StatsTimingEvent = mock_db.add.call_args_list[1][0][0]
        assert isinstance(timing, StatsTimingEvent)
        assert timing.event_name == "search_started"
        assert timing.request_id == 42
        assert mock_db.flush.await_count == 2
        assert result is added

    @pytest.mark.asyncio
    async def test_log_with_none_request_id(self, mock_db, service):
        """log() works for system events with no request_id."""
        result = await service.log(EventType.ERROR, details={"msg": "oops"})

        added: ActivityLog = mock_db.add.call_args[0][0]
        assert added.request_id is None
        assert added.event_type == "error"
        assert result is added

    @pytest.mark.asyncio
    async def test_log_with_no_details(self, mock_db, service):
        """log() stores None when no details provided."""
        await service.log(EventType.DOWNLOAD_COMPLETED, request_id=1)

        added: ActivityLog = mock_db.add.call_args[0][0]
        assert added.details is None

    @pytest.mark.asyncio
    async def test_get_timeline_filters_by_request_id(self, mock_db, service):
        """get_timeline() executes a query filtered by request_id."""
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [
            MagicMock(spec=ActivityLog),
        ]
        mock_db.execute.return_value = mock_result

        logs = await service.get_timeline(request_id=7, limit=10)

        mock_db.execute.assert_awaited_once()
        assert len(logs) == 1

    @pytest.mark.asyncio
    async def test_get_recent_returns_list(self, mock_db, service):
        """get_recent() returns recent logs."""
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_db.execute.return_value = mock_result

        logs = await service.get_recent(limit=5)

        mock_db.execute.assert_awaited_once()
        assert logs == []

    @pytest.mark.asyncio
    async def test_log_swallows_flush_exception(self, mock_db, service):
        """log() swallows a flush exception, logs it, and returns None."""
        mock_db.flush.side_effect = RuntimeError("db down")

        with patch(
            "app.siftarr.services.lifecycle.activity_log_service.logger.exception"
        ) as mock_log_exception:
            result = await service.log(EventType.SEARCH_STARTED, request_id=99)

        assert result is None
        mock_db.flush.assert_awaited_once()
        mock_log_exception.assert_called_once_with("Failed to log activity for request_id=%s", 99)
