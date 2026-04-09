"""Tests for PendingQueueService."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.siftarr.models.pending_queue import PendingQueue
from app.siftarr.models.request import Request, RequestStatus
from app.siftarr.services.pending_queue_service import PendingQueueService


class TestPendingQueueService:
    """Test cases for PendingQueueService."""

    @pytest.fixture
    def mock_db(self):
        """Create a mock database session."""
        return AsyncMock()

    @pytest.fixture
    def service(self, mock_db):
        """Create a PendingQueueService instance."""
        return PendingQueueService(mock_db)

    @pytest.mark.asyncio
    async def test_add_to_queue_new(self, mock_db, service):
        """Test adding a new item to the queue."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result
        mock_db.add = MagicMock()
        mock_db.commit = AsyncMock()
        mock_db.refresh = AsyncMock()

        await service.add_to_queue(
            request_id=1,
            retry_interval_hours=24,
            error_message=None,
        )

        mock_db.add.assert_called_once()
        mock_db.commit.assert_called_once()
        mock_db.refresh.assert_called_once()

    @pytest.mark.asyncio
    async def test_add_to_queue_existing(self, mock_db, service):
        """Test adding an item that's already in the queue."""
        existing = MagicMock(spec=PendingQueue)
        existing.retry_count = 1

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = existing
        mock_db.execute.return_value = mock_result
        mock_db.commit = AsyncMock()
        mock_db.refresh = AsyncMock()

        await service.add_to_queue(request_id=1)

        assert existing.retry_count == 2
        mock_db.add.assert_not_called()
        mock_db.commit.assert_called()

    @pytest.mark.asyncio
    async def test_get_by_request_id(self, mock_db, service):
        """Test getting a queue entry by request ID."""
        mock_entry = MagicMock(spec=PendingQueue)
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_entry
        mock_db.execute.return_value = mock_result

        result = await service.get_by_request_id(1)

        assert result == mock_entry

    @pytest.mark.asyncio
    async def test_get_by_request_id_not_found(self, mock_db, service):
        """Test getting a non-existent queue entry."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result

        result = await service.get_by_request_id(999)

        assert result is None

    @pytest.mark.asyncio
    async def test_get_ready_for_retry(self, mock_db, service):
        """Test getting items ready for retry."""
        now = datetime.now(UTC)
        mock_entries = [
            MagicMock(spec=PendingQueue, request_id=1, next_retry_at=now - timedelta(hours=1)),
            MagicMock(spec=PendingQueue, request_id=2, next_retry_at=now - timedelta(hours=2)),
        ]
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = mock_entries
        mock_db.execute.return_value = mock_result

        result = await service.get_ready_for_retry()

        assert len(result) == 2
        mock_db.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_all_pending(self, mock_db, service):
        """Test getting all pending items."""
        mock_entries = [
            MagicMock(spec=PendingQueue, request_id=1),
            MagicMock(spec=PendingQueue, request_id=2),
        ]
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = mock_entries
        mock_db.execute.return_value = mock_result

        result = await service.get_all_pending()

        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_remove_from_queue(self, mock_db, service):
        """Test removing an item from the queue."""
        mock_entry = MagicMock(spec=PendingQueue)
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_entry
        mock_db.execute.return_value = mock_result
        mock_db.delete = AsyncMock()
        mock_db.commit = AsyncMock()

        result = await service.remove_from_queue(1)

        assert result is True
        mock_db.delete.assert_called_once_with(mock_entry)
        mock_db.commit.assert_called()

    @pytest.mark.asyncio
    async def test_remove_from_queue_not_found(self, mock_db, service):
        """Test removing a non-existent item."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result

        result = await service.remove_from_queue(999)

        assert result is False
        mock_db.delete.assert_not_called()

    @pytest.mark.asyncio
    async def test_mark_retry_failed_under_max(self, mock_db, service):
        """Test marking retry as failed when under max retries."""
        mock_entry = MagicMock(spec=PendingQueue)
        mock_entry.retry_count = 3
        mock_entry.next_retry_at = datetime.now(UTC)

        mock_request = MagicMock(spec=Request)

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.side_effect = [mock_entry, mock_request]
        mock_db.execute.return_value = mock_result
        mock_db.delete = AsyncMock()
        mock_db.commit = AsyncMock()

        updated, max_exceeded = await service.mark_retry_failed(request_id=1, max_retries=7)

        assert updated is True
        assert max_exceeded is False
        assert mock_entry.retry_count == 4
        mock_db.delete.assert_not_called()

    @pytest.mark.asyncio
    async def test_mark_retry_failed_at_max(self, mock_db, service):
        """Test marking retry as failed when at max retries."""
        mock_entry = MagicMock(spec=PendingQueue)
        mock_entry.retry_count = 6

        mock_request = MagicMock(spec=Request)

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.side_effect = [mock_entry, mock_request]
        mock_db.execute.return_value = mock_result
        mock_db.delete = AsyncMock()
        mock_db.commit = AsyncMock()

        updated, max_exceeded = await service.mark_retry_failed(request_id=1, max_retries=7)

        assert updated is True
        assert max_exceeded is True
        assert mock_request.status == RequestStatus.FAILED
        mock_db.delete.assert_called_once_with(mock_entry)

    @pytest.mark.asyncio
    async def test_mark_retry_failed_not_found(self, mock_db, service):
        """Test marking retry failed for non-existent entry."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result

        updated, max_exceeded = await service.mark_retry_failed(request_id=999)

        assert updated is False
        assert max_exceeded is False

    @pytest.mark.asyncio
    async def test_update_error(self, mock_db, service):
        """Test updating error message."""
        mock_entry = MagicMock(spec=PendingQueue)
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_entry
        mock_db.execute.return_value = mock_result
        mock_db.commit = AsyncMock()

        result = await service.update_error(request_id=1, error_message="Test error")

        assert result is True
        assert mock_entry.last_error == "Test error"
        mock_db.commit.assert_called()

    @pytest.mark.asyncio
    async def test_update_error_not_found(self, mock_db, service):
        """Test updating error for non-existent entry."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result

        result = await service.update_error(request_id=999, error_message="Test error")

        assert result is False

    @pytest.mark.asyncio
    async def test_update_error_truncation(self, mock_db, service):
        """Test that error messages are truncated to 500 chars."""
        mock_entry = MagicMock(spec=PendingQueue)
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_entry
        mock_db.execute.return_value = mock_result
        mock_db.commit = AsyncMock()

        long_error = "x" * 600
        await service.update_error(request_id=1, error_message=long_error)

        assert len(mock_entry.last_error) == 500

    @pytest.mark.asyncio
    async def test_get_queue_stats(self, mock_db, service):
        """Test getting queue statistics using SQL aggregates."""
        now = datetime.now(UTC)
        total_result = MagicMock()
        total_result.scalar.return_value = 3
        ready_result = MagicMock()
        ready_result.scalar.return_value = 2
        oldest_result = MagicMock()
        oldest_result.scalar.return_value = now - timedelta(hours=2)

        mock_db.execute.side_effect = [total_result, ready_result, oldest_result]

        result = await service.get_queue_stats()

        assert result["total_pending"] == 3
        assert result["ready_for_retry"] == 2
        assert result["waiting_for_retry"] == 1
        assert result["oldest_pending"] is not None

    @pytest.mark.asyncio
    async def test_get_queue_stats_empty(self, mock_db, service):
        """Test getting stats for empty queue."""
        total_result = MagicMock()
        total_result.scalar.return_value = 0
        ready_result = MagicMock()
        ready_result.scalar.return_value = 0
        oldest_result = MagicMock()
        oldest_result.scalar.return_value = None

        mock_db.execute.side_effect = [total_result, ready_result, oldest_result]

        result = await service.get_queue_stats()

        assert result["total_pending"] == 0
        assert result["ready_for_retry"] == 0
        assert result["waiting_for_retry"] == 0
        assert result["oldest_pending"] is None
