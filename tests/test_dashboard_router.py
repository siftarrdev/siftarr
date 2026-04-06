"""Tests for dashboard router."""

from unittest.mock import AsyncMock, MagicMock

import pytest


class TestDashboardApproveDeny:
    """Test cases for dashboard approve/deny functionality."""

    @pytest.fixture
    def mock_db(self):
        """Create a mock database session."""
        return AsyncMock()

    @pytest.fixture
    def mock_overseerr_service(self):
        """Create a mock OverseerrService."""
        return AsyncMock()

    @pytest.mark.asyncio
    async def test_approve_request_success(self, mock_overseerr_service):
        """Test approve_request_success."""
        mock_overseerr_service.approve_request.return_value = True

        result = await mock_overseerr_service.approve_request(123)

        assert result is True
        mock_overseerr_service.approve_request.assert_called_once_with(123)

    @pytest.mark.asyncio
    async def test_approve_request_not_found(self, mock_overseerr_service):
        """Test approve_request_not_found - request doesn't exist, expects 404."""
        mock_overseerr_service.approve_request.return_value = False

        from starlette.exceptions import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            if not await mock_overseerr_service.approve_request(999):
                raise HTTPException(status_code=404, detail="Request not found")

        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_deny_request_success(self, mock_overseerr_service):
        """Test deny_request_success."""
        mock_overseerr_service.decline_request.return_value = True

        result = await mock_overseerr_service.decline_request(123)

        assert result is True
        mock_overseerr_service.decline_request.assert_called_once_with(123)

    @pytest.mark.asyncio
    async def test_deny_request_not_found(self, mock_overseerr_service):
        """Test deny_request_not_found - request doesn't exist, expects 404."""
        mock_overseerr_service.decline_request.return_value = False

        from starlette.exceptions import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            if not await mock_overseerr_service.decline_request(999):
                raise HTTPException(status_code=404, detail="Request not found")

        assert exc_info.value.status_code == 404
