"""Tests for LifecycleService."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.siftarr.models.request import Request, RequestStatus
from app.siftarr.services.lifecycle_service import LifecycleService


class TestLifecycleService:
    """Test cases for LifecycleService."""

    @pytest.fixture
    def mock_db(self):
        """Create a mock database session."""
        return AsyncMock()

    @pytest.fixture
    def service(self, mock_db):
        """Create a LifecycleService instance."""
        return LifecycleService(mock_db)

    def test_can_transition_valid(self, service):
        """Test valid status transitions."""
        assert service.can_transition(RequestStatus.RECEIVED, RequestStatus.SEARCHING)
        assert service.can_transition(RequestStatus.RECEIVED, RequestStatus.FAILED)
        assert service.can_transition(RequestStatus.SEARCHING, RequestStatus.PENDING)
        assert service.can_transition(RequestStatus.SEARCHING, RequestStatus.STAGED)
        assert service.can_transition(RequestStatus.SEARCHING, RequestStatus.DOWNLOADING)
        assert service.can_transition(RequestStatus.SEARCHING, RequestStatus.COMPLETED)
        assert service.can_transition(RequestStatus.SEARCHING, RequestStatus.FAILED)
        assert service.can_transition(RequestStatus.PENDING, RequestStatus.SEARCHING)
        assert service.can_transition(RequestStatus.STAGED, RequestStatus.DOWNLOADING)
        assert service.can_transition(RequestStatus.STAGED, RequestStatus.PENDING)
        assert service.can_transition(RequestStatus.DOWNLOADING, RequestStatus.COMPLETED)
        assert service.can_transition(RequestStatus.DOWNLOADING, RequestStatus.FAILED)

    @pytest.mark.asyncio
    async def test_get_active_requests_includes_searching(self, mock_db, service):
        """Searching requests should be returned as active."""
        mock_requests = [MagicMock(spec=Request, id=1, status=RequestStatus.SEARCHING)]
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = mock_requests
        mock_db.execute.return_value = mock_result

        result = await service.get_active_requests()

        assert len(result) == 1
        assert result[0].status == RequestStatus.SEARCHING

    def test_can_transition_invalid(self, service):
        """Test invalid status transitions."""
        assert not service.can_transition(RequestStatus.RECEIVED, RequestStatus.COMPLETED)
        assert not service.can_transition(RequestStatus.COMPLETED, RequestStatus.SEARCHING)
        assert not service.can_transition(RequestStatus.FAILED, RequestStatus.SEARCHING)
        assert not service.can_transition(RequestStatus.STAGED, RequestStatus.COMPLETED)
        assert not service.can_transition(RequestStatus.PENDING, RequestStatus.RECEIVED)

    def test_can_transition_terminal_states(self, service):
        """Test that terminal states have no valid transitions."""
        assert not service.can_transition(RequestStatus.COMPLETED, RequestStatus.FAILED)
        assert not service.can_transition(RequestStatus.COMPLETED, RequestStatus.RECEIVED)
        assert not service.can_transition(RequestStatus.FAILED, RequestStatus.COMPLETED)
        assert not service.can_transition(RequestStatus.FAILED, RequestStatus.RECEIVED)

    @pytest.mark.asyncio
    async def test_transition_success(self, mock_db, service):
        """Test successful status transition."""
        mock_request = MagicMock(spec=Request)
        mock_request.status = RequestStatus.RECEIVED
        mock_request.id = 1

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_request
        mock_db.execute.return_value = mock_result

        result = await service.transition(1, RequestStatus.SEARCHING)

        assert result == mock_request
        assert result.status == RequestStatus.SEARCHING
        mock_db.commit.assert_called_once()
        mock_db.refresh.assert_called_once()

    @pytest.mark.asyncio
    async def test_transition_request_not_found(self, mock_db, service):
        """Test transition when request doesn't exist."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result

        result = await service.transition(999, RequestStatus.SEARCHING)

        assert result is None
        mock_db.commit.assert_not_called()

    @pytest.mark.asyncio
    async def test_transition_invalid_transition(self, mock_db, service):
        """Test transition with invalid status change."""
        mock_request = MagicMock(spec=Request)
        mock_request.status = RequestStatus.COMPLETED
        mock_request.id = 1

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_request
        mock_db.execute.return_value = mock_result

        with pytest.raises(ValueError, match="Invalid transition"):
            await service.transition(1, RequestStatus.SEARCHING)

    @pytest.mark.asyncio
    async def test_get_request_status(self, mock_db, service):
        """Test getting request status."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = RequestStatus.SEARCHING
        mock_db.execute.return_value = mock_result

        result = await service.get_request_status(1)

        assert result == RequestStatus.SEARCHING

    @pytest.mark.asyncio
    async def test_get_active_requests(self, mock_db, service):
        """Test getting active requests."""
        mock_requests = [
            MagicMock(spec=Request, id=1, status=RequestStatus.SEARCHING),
            MagicMock(spec=Request, id=2, status=RequestStatus.PENDING),
        ]
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = mock_requests
        mock_db.execute.return_value = mock_result

        result = await service.get_active_requests()

        assert len(result) == 2
        mock_db.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_requests_by_status(self, mock_db, service):
        """Test getting requests by specific status."""
        mock_requests = [
            MagicMock(spec=Request, id=1, status=RequestStatus.PENDING),
        ]
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = mock_requests
        mock_db.execute.return_value = mock_result

        result = await service.get_requests_by_status(RequestStatus.PENDING)

        assert len(result) == 1
        assert result[0].status == RequestStatus.PENDING

    @pytest.mark.asyncio
    async def test_get_requests_stats(self, mock_db, service):
        """Test getting request statistics using SQL aggregates."""
        mock_result = MagicMock()
        mock_result.all.return_value = [
            (RequestStatus.COMPLETED, 2),
            (RequestStatus.PENDING, 1),
            (RequestStatus.FAILED, 1),
        ]
        mock_db.execute.return_value = mock_result

        result = await service.get_requests_stats()

        assert result["total"] == 4
        assert result["by_status"]["completed"] == 2
        assert result["by_status"]["pending"] == 1
        assert result["by_status"]["failed"] == 1

    @pytest.mark.asyncio
    async def test_update_request_metadata(self, mock_db, service):
        """Test updating request metadata."""
        mock_request = MagicMock(spec=Request)
        mock_request.id = 1
        mock_request.title = "Old Title"
        mock_request.year = 2020

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_request
        mock_db.execute.return_value = mock_result

        result = await service.update_request_metadata(1, title="New Title", year=2024)

        assert result.title == "New Title"
        assert result.year == 2024
        mock_db.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_update_request_metadata_not_found(self, mock_db, service):
        """Test updating metadata for non-existent request."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result

        result = await service.update_request_metadata(999, title="New Title")

        assert result is None

    @pytest.mark.asyncio
    async def test_mark_as_staged(self, mock_db, service):
        """Test marking request as staged."""
        mock_request = MagicMock(spec=Request)
        mock_request.status = RequestStatus.SEARCHING
        mock_request.id = 1

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_request
        mock_db.execute.return_value = mock_result

        with patch.object(service, "transition", return_value=mock_request) as mock_transition:
            result = await service.mark_as_staged(1)

            mock_transition.assert_called_once_with(1, RequestStatus.STAGED)
            assert result == mock_request

    @pytest.mark.asyncio
    async def test_mark_as_downloading(self, mock_db, service):
        """Test marking request as downloading."""
        mock_request = MagicMock(spec=Request)
        mock_request.status = RequestStatus.STAGED
        mock_request.id = 1

        with patch.object(service, "transition", return_value=mock_request) as mock_transition:
            await service.mark_as_downloading(1)

            mock_transition.assert_called_once_with(1, RequestStatus.DOWNLOADING)

    @pytest.mark.asyncio
    async def test_mark_as_completed(self, mock_db, service):
        """Test marking request as completed."""
        mock_request = MagicMock(spec=Request)

        with patch.object(service, "transition", return_value=mock_request) as mock_transition:
            await service.mark_as_completed(1)

            mock_transition.assert_called_once_with(1, RequestStatus.COMPLETED)

    @pytest.mark.asyncio
    async def test_mark_as_failed(self, mock_db, service):
        """Test marking request as failed."""
        mock_request = MagicMock(spec=Request)

        with patch.object(service, "transition", return_value=mock_request) as mock_transition:
            await service.mark_as_failed(1, reason="Test error")

            mock_transition.assert_called_once_with(1, RequestStatus.FAILED, "Test error")

    @pytest.mark.asyncio
    async def test_mark_as_pending(self, mock_db, service):
        """Test marking request as pending."""
        mock_request = MagicMock(spec=Request)

        with patch.object(service, "transition", return_value=mock_request) as mock_transition:
            await service.mark_as_pending(1)

            mock_transition.assert_called_once_with(1, RequestStatus.PENDING)

    def test_can_transition_to_denied_from_non_terminal(self, service):
        """Test that DENIED is reachable from all non-terminal states."""
        non_terminal = [
            RequestStatus.RECEIVED,
            RequestStatus.SEARCHING,
            RequestStatus.PENDING,
            RequestStatus.STAGED,
            RequestStatus.DOWNLOADING,
        ]
        for status in non_terminal:
            assert service.can_transition(status, RequestStatus.DENIED), (
                f"Should allow transition from {status} to DENIED"
            )

    def test_denied_is_terminal(self, service):
        """Test that DENIED is a terminal state with no outgoing transitions."""
        for status in RequestStatus:
            assert not service.can_transition(RequestStatus.DENIED, status), (
                f"DENIED should not transition to {status}"
            )

    @pytest.mark.asyncio
    async def test_mark_as_denied(self, mock_db, service):
        """Test marking request as denied."""
        mock_request = MagicMock(spec=Request)

        with patch.object(service, "transition", return_value=mock_request) as mock_transition:
            await service.mark_as_denied(1, reason="Not wanted")

            mock_transition.assert_called_once_with(1, RequestStatus.DENIED, "Not wanted")
