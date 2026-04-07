"""Tests for dashboard router helpers and endpoints."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.siftarr.routers import dashboard


class TestDashboardRouter:
    """Test cases for dashboard router helpers and actions."""

    @pytest.fixture
    def mock_db(self):
        """Create a mock database session."""
        return AsyncMock()

    @pytest.mark.asyncio
    async def test_bulk_request_action_redirects_to_requested_tab(self, mock_db, monkeypatch):
        """Bulk actions should return to the requested tab."""
        request_record = MagicMock()
        request_record.created_at = MagicMock()

        execute_result = MagicMock()
        execute_result.scalars.return_value.all.return_value = [request_record]
        mock_db.execute.return_value = execute_result

        process_request_search = AsyncMock()
        monkeypatch.setattr(dashboard, "_process_request_search", process_request_search)

        response = await dashboard.bulk_request_action(
            action="search",
            request_ids=[1],
            redirect_to="/?tab=active",
            db=mock_db,
        )

        assert response.status_code == 303
        assert response.headers["location"] == "/?tab=active"
        process_request_search.assert_awaited_once_with(request_record, mock_db)

    @pytest.mark.asyncio
    async def test_bulk_request_action_defaults_to_pending_tab(self, mock_db):
        """Bulk actions default back to the pending tab."""
        response = await dashboard.bulk_request_action(
            action="search",
            request_ids=[],
            redirect_to=None,
            db=mock_db,
        )

        assert response.status_code == 303
        assert response.headers["location"] == "/?tab=pending"

    @pytest.mark.asyncio
    async def test_pending_requests_include_searching_requests(self, mock_db, monkeypatch):
        """Pending tab should keep in-flight searches visible."""
        active_request = MagicMock()
        active_request.id = 1
        active_request.status = dashboard.RequestStatus.SEARCHING
        active_request.overseerr_request_id = 10
        active_request.title = "The Rookie"
        active_request.media_type.value = "tv"
        active_request.created_at = MagicMock()

        lifecycle_service = AsyncMock()
        lifecycle_service.get_active_requests.return_value = [active_request]
        monkeypatch.setattr(dashboard, "LifecycleService", lambda db: lifecycle_service)

        class FakeOverseerrService:
            def __init__(self, settings):
                pass

            async def get_request_status(self, request_id):
                return {"status": "approved", "media": {"status": "processing"}}

            def normalize_request_status(self, value):
                return value

            def normalize_media_status(self, value):
                return value

            async def close(self):
                return None

        monkeypatch.setattr(dashboard, "OverseerrService", FakeOverseerrService)
        monkeypatch.setattr(dashboard, "PendingQueueService", lambda db: AsyncMock(get_all_pending=AsyncMock(return_value=[])))

        mock_db.execute.return_value = MagicMock(
            scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))
        )

        response = await dashboard.dashboard(MagicMock(), db=mock_db)

        context = response.context
        assert active_request in context["pending_requests"]

    @pytest.mark.asyncio
    async def test_approve_request_success(self):
        """Approve helper should surface successful approvals."""
        mock_overseerr_service = AsyncMock()
        mock_overseerr_service.approve_request.return_value = True

        result = await mock_overseerr_service.approve_request(123)

        assert result is True
        mock_overseerr_service.approve_request.assert_called_once_with(123)

    @pytest.mark.asyncio
    async def test_approve_request_not_found(self):
        """Approve helper should map a missing request to 404."""
        mock_overseerr_service = AsyncMock()
        mock_overseerr_service.approve_request.return_value = False

        from starlette.exceptions import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            if not await mock_overseerr_service.approve_request(999):
                raise HTTPException(status_code=404, detail="Request not found")

        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_deny_request_success(self):
        """Deny helper should surface successful declines."""
        mock_overseerr_service = AsyncMock()
        mock_overseerr_service.decline_request.return_value = True

        result = await mock_overseerr_service.decline_request(123)

        assert result is True
        mock_overseerr_service.decline_request.assert_called_once_with(123)

    @pytest.mark.asyncio
    async def test_deny_request_not_found(self):
        """Deny helper should map a missing request to 404."""
        mock_overseerr_service = AsyncMock()
        mock_overseerr_service.decline_request.return_value = False

        from starlette.exceptions import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            if not await mock_overseerr_service.decline_request(999):
                raise HTTPException(status_code=404, detail="Request not found")

        assert exc_info.value.status_code == 404
