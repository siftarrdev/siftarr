"""Tests for OverseerrService."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.siftarr.services import overseerr_service
from app.siftarr.services.overseerr_service import OverseerrService


class TestOverseerrService:
    """Test cases for OverseerrService."""

    def test_init(self):
        """Test service initialization."""
        with patch("app.siftarr.services.overseerr_service.get_settings") as mock_get_settings:
            mock_settings = MagicMock()
            mock_settings.overseerr_url = "http://localhost:5055"
            mock_settings.overseerr_api_key = "test_api_key"
            mock_get_settings.return_value = mock_settings

            service = OverseerrService()

            assert service.base_url == "http://localhost:5055"
            assert service.api_key == "test_api_key"

    def test_init_strips_trailing_slash(self):
        """Test that URL trailing slash is stripped."""
        with patch("app.siftarr.services.overseerr_service.get_settings") as mock_get_settings:
            mock_settings = MagicMock()
            mock_settings.overseerr_url = "http://localhost:5055/"
            mock_settings.overseerr_api_key = "test_api_key"
            mock_get_settings.return_value = mock_settings

            service = OverseerrService()

            assert service.base_url == "http://localhost:5055"

    @pytest.mark.asyncio
    async def test_close_is_noop(self):
        """Test closing is a no-op with shared client."""
        with patch("app.siftarr.services.overseerr_service.get_settings") as mock_get_settings:
            mock_settings = MagicMock()
            mock_settings.overseerr_url = "http://localhost:5055"
            mock_settings.overseerr_api_key = "test"
            mock_get_settings.return_value = mock_settings

            service = OverseerrService()
            await service.close()

    @pytest.mark.asyncio
    async def test_get_requests_no_url(self):
        """Test get_requests when URL is empty."""
        with patch("app.siftarr.services.overseerr_service.get_settings") as mock_get_settings:
            mock_settings = MagicMock()
            mock_settings.overseerr_url = ""
            mock_settings.overseerr_api_key = "test"
            mock_get_settings.return_value = mock_settings

            service = OverseerrService()
            result = await service.get_requests()

            assert result == []

    @pytest.mark.asyncio
    async def test_get_requests_no_api_key(self):
        """Test get_requests when API key is empty."""
        with patch("app.siftarr.services.overseerr_service.get_settings") as mock_get_settings:
            mock_settings = MagicMock()
            mock_settings.overseerr_url = "http://localhost:5055"
            mock_settings.overseerr_api_key = ""
            mock_get_settings.return_value = mock_settings

            service = OverseerrService()
            result = await service.get_requests()

            assert result == []

    @pytest.mark.asyncio
    async def test_get_requests_success(self):
        """Test successful get_requests."""
        with patch("app.siftarr.services.overseerr_service.get_settings") as mock_get_settings:
            mock_settings = MagicMock()
            mock_settings.overseerr_url = "http://localhost:5055"
            mock_settings.overseerr_api_key = "test"
            mock_get_settings.return_value = mock_settings

            service = OverseerrService()
            mock_client = AsyncMock()
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {"results": [{"id": 1}, {"id": 2}]}
            mock_client.get = AsyncMock(return_value=mock_response)

            with patch(
                "app.siftarr.services.overseerr_service.get_shared_client",
                return_value=mock_client,
            ):
                result = await service.get_requests()

            assert len(result) == 2
            mock_client.get.assert_called_once()
            call_kwargs = mock_client.get.call_args
            assert call_kwargs[0][0] == "http://localhost:5055/api/v1/request"

    @pytest.mark.asyncio
    async def test_get_requests_without_filter(self):
        """Test get_requests omits filter when status is not provided."""
        with patch("app.siftarr.services.overseerr_service.get_settings") as mock_get_settings:
            mock_settings = MagicMock()
            mock_settings.overseerr_url = "http://localhost:5055"
            mock_settings.overseerr_api_key = "test"
            mock_get_settings.return_value = mock_settings

            service = OverseerrService()
            mock_client = AsyncMock()
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {"results": []}
            mock_client.get = AsyncMock(return_value=mock_response)

            with patch(
                "app.siftarr.services.overseerr_service.get_shared_client",
                return_value=mock_client,
            ):
                await service.get_requests(status=None, limit=50, skip=100)

            mock_client.get.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_all_requests_paginates(self):
        """Test get_all_requests aggregates paginated responses."""
        with patch("app.siftarr.services.overseerr_service.get_settings") as mock_get_settings:
            mock_settings = MagicMock()
            mock_settings.overseerr_url = "http://localhost:5055"
            mock_settings.overseerr_api_key = "test"
            mock_get_settings.return_value = mock_settings

            service = OverseerrService()

            with patch.object(
                service,
                "get_requests",
                new=AsyncMock(side_effect=[[{"id": 1}], [{"id": 2}], []]),
            ) as mock_get_requests:
                result = await service.get_all_requests(status=None, page_size=1)

                assert result == [{"id": 1}, {"id": 2}]
                assert mock_get_requests.await_count == 3

    def test_normalize_media_status_numeric(self):
        """Test numeric media statuses are normalized."""
        assert OverseerrService.normalize_media_status(2) == "pending"
        assert OverseerrService.normalize_media_status(4) == "partially_available"
        assert OverseerrService.normalize_media_status(5) == "available"

    def test_normalize_request_status_numeric(self):
        """Test numeric request statuses are normalized."""
        assert OverseerrService.normalize_request_status(1) == "pending"
        assert OverseerrService.normalize_request_status(2) == "approved"
        assert OverseerrService.normalize_request_status(5) == "completed"

    @pytest.mark.asyncio
    async def test_get_requests_unauthorized(self):
        """Test get_requests with 401 response."""
        with patch("app.siftarr.services.overseerr_service.get_settings") as mock_get_settings:
            mock_settings = MagicMock()
            mock_settings.overseerr_url = "http://localhost:5055"
            mock_settings.overseerr_api_key = "test"
            mock_get_settings.return_value = mock_settings

            service = OverseerrService()
            mock_client = AsyncMock()
            mock_response = MagicMock()
            mock_response.status_code = 401
            mock_client.get = AsyncMock(return_value=mock_response)

            with patch(
                "app.siftarr.services.overseerr_service.get_shared_client",
                return_value=mock_client,
            ):
                result = await service.get_requests()

            assert result == []

    @pytest.mark.asyncio
    async def test_get_requests_network_error(self):
        """Test get_requests with network error."""
        import httpx

        with patch("app.siftarr.services.overseerr_service.get_settings") as mock_get_settings:
            mock_settings = MagicMock()
            mock_settings.overseerr_url = "http://localhost:5055"
            mock_settings.overseerr_api_key = "test"
            mock_get_settings.return_value = mock_settings

            service = OverseerrService()
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(side_effect=httpx.RequestError("Network error"))

            with patch(
                "app.siftarr.services.overseerr_service.get_shared_client",
                return_value=mock_client,
            ):
                result = await service.get_requests()

            assert result == []

    @pytest.mark.asyncio
    async def test_get_request_no_url(self):
        """Test get_request when not configured."""
        with patch("app.siftarr.services.overseerr_service.get_settings") as mock_get_settings:
            mock_settings = MagicMock()
            mock_settings.overseerr_url = ""
            mock_settings.overseerr_api_key = "test"
            mock_get_settings.return_value = mock_settings

            service = OverseerrService()
            result = await service.get_request(1)

            assert result is None

    @pytest.mark.asyncio
    async def test_get_media_details_no_url(self):
        """Test get_media_details when not configured."""
        with patch("app.siftarr.services.overseerr_service.get_settings") as mock_get_settings:
            mock_settings = MagicMock()
            mock_settings.overseerr_url = ""
            mock_settings.overseerr_api_key = "test"
            mock_get_settings.return_value = mock_settings

            service = OverseerrService()
            result = await service.get_media_details("movie", 123)

            assert result is None

    @pytest.mark.asyncio
    async def test_get_media_details_success(self):
        """Test successful get_media_details."""
        with patch("app.siftarr.services.overseerr_service.get_settings") as mock_get_settings:
            mock_settings = MagicMock()
            mock_settings.overseerr_url = "http://localhost:5055"
            mock_settings.overseerr_api_key = "test"
            mock_get_settings.return_value = mock_settings

            service = OverseerrService()
            mock_client = AsyncMock()
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {"id": 123, "title": "Test Movie"}
            mock_client.get = AsyncMock(return_value=mock_response)

            with patch(
                "app.siftarr.services.overseerr_service.get_shared_client",
                return_value=mock_client,
            ):
                result = await service.get_media_details("movie", 123)

                assert result is not None
                assert result["id"] == 123

    @pytest.mark.asyncio
    async def test_get_media_details_not_found(self):
        """Test get_media_details with 404 response."""
        with patch("app.siftarr.services.overseerr_service.get_settings") as mock_get_settings:
            mock_settings = MagicMock()
            mock_settings.overseerr_url = "http://localhost:5055"
            mock_settings.overseerr_api_key = "test"
            mock_get_settings.return_value = mock_settings

            service = OverseerrService()
            mock_client = AsyncMock()
            mock_response = MagicMock()
            mock_response.status_code = 404
            mock_client.get = AsyncMock(return_value=mock_response)

            with patch(
                "app.siftarr.services.overseerr_service.get_shared_client",
                return_value=mock_client,
            ):
                result = await service.get_media_details("movie", 999)

            assert result is None

    @pytest.mark.asyncio
    async def test_approve_request_success(self):
        """Test approving a request successfully."""
        with patch("app.siftarr.services.overseerr_service.get_settings") as mock_get_settings:
            mock_settings = MagicMock()
            mock_settings.overseerr_url = "http://localhost:5055"
            mock_settings.overseerr_api_key = "test"
            mock_get_settings.return_value = mock_settings

            service = OverseerrService()
            mock_client = AsyncMock()
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_client.post = AsyncMock(return_value=mock_response)

            with patch(
                "app.siftarr.services.overseerr_service.get_shared_client",
                return_value=mock_client,
            ):
                result = await service.approve_request(123)

            assert result is True
            mock_client.post.assert_called_once()

    @pytest.mark.asyncio
    async def test_approve_request_failure(self):
        """Test approve returns False on failure."""
        with patch("app.siftarr.services.overseerr_service.get_settings") as mock_get_settings:
            mock_settings = MagicMock()
            mock_settings.overseerr_url = "http://localhost:5055"
            mock_settings.overseerr_api_key = "test"
            mock_get_settings.return_value = mock_settings

            service = OverseerrService()
            mock_client = AsyncMock()
            mock_response = MagicMock()
            mock_response.status_code = 500
            mock_client.post = AsyncMock(return_value=mock_response)

            with patch(
                "app.siftarr.services.overseerr_service.get_shared_client",
                return_value=mock_client,
            ):
                result = await service.approve_request(123)

            assert result is False

    @pytest.mark.asyncio
    async def test_decline_request_success(self):
        """Test declining a request successfully."""
        with patch("app.siftarr.services.overseerr_service.get_settings") as mock_get_settings:
            mock_settings = MagicMock()
            mock_settings.overseerr_url = "http://localhost:5055"
            mock_settings.overseerr_api_key = "test"
            mock_get_settings.return_value = mock_settings

            service = OverseerrService()
            mock_client = AsyncMock()
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_client.post = AsyncMock(return_value=mock_response)

            with patch(
                "app.siftarr.services.overseerr_service.get_shared_client",
                return_value=mock_client,
            ):
                result = await service.decline_request(123)

            assert result is True
            mock_client.post.assert_called_once()

    @pytest.mark.asyncio
    async def test_decline_request_with_reason(self):
        """Test decline with reason in body."""
        with patch("app.siftarr.services.overseerr_service.get_settings") as mock_get_settings:
            mock_settings = MagicMock()
            mock_settings.overseerr_url = "http://localhost:5055"
            mock_settings.overseerr_api_key = "test"
            mock_get_settings.return_value = mock_settings

            service = OverseerrService()
            mock_client = AsyncMock()
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_client.post = AsyncMock(return_value=mock_response)

            with patch(
                "app.siftarr.services.overseerr_service.get_shared_client",
                return_value=mock_client,
            ):
                result = await service.decline_request(123, reason="Test reason")

            assert result is True
            mock_client.post.assert_called_once()
            call_args = mock_client.post.call_args
            assert call_args[1]["json"] == {"reason": "Test reason"}

    @pytest.mark.asyncio
    async def test_decline_request_failure(self):
        """Test decline returns False on failure."""
        with patch("app.siftarr.services.overseerr_service.get_settings") as mock_get_settings:
            mock_settings = MagicMock()
            mock_settings.overseerr_url = "http://localhost:5055"
            mock_settings.overseerr_api_key = "test"
            mock_get_settings.return_value = mock_settings

            service = OverseerrService()
            mock_client = AsyncMock()
            mock_response = MagicMock()
            mock_response.status_code = 400
            mock_client.post = AsyncMock(return_value=mock_response)

            with patch(
                "app.siftarr.services.overseerr_service.get_shared_client",
                return_value=mock_client,
            ):
                result = await service.decline_request(123)

            assert result is False

    @pytest.mark.asyncio
    async def test_get_request_status_success(self):
        """Test getting request status successfully."""
        with patch("app.siftarr.services.overseerr_service.get_settings") as mock_get_settings:
            mock_settings = MagicMock()
            mock_settings.overseerr_url = "http://localhost:5055"
            mock_settings.overseerr_api_key = "test"
            mock_get_settings.return_value = mock_settings

            service = OverseerrService()
            mock_client = AsyncMock()
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {"id": 123, "status": "approved"}
            mock_client.get = AsyncMock(return_value=mock_response)

            with patch(
                "app.siftarr.services.overseerr_service.get_shared_client",
                return_value=mock_client,
            ):
                result = await service.get_request_status(123)

            assert result == {"id": 123, "status": "approved"}

    @pytest.mark.asyncio
    async def test_get_request_status_failure(self):
        """Test get_request_status returns None on failure."""
        with patch("app.siftarr.services.overseerr_service.get_settings") as mock_get_settings:
            mock_settings = MagicMock()
            mock_settings.overseerr_url = "http://localhost:5055"
            mock_settings.overseerr_api_key = "test"
            mock_get_settings.return_value = mock_settings

            service = OverseerrService()
            mock_client = AsyncMock()
            mock_response = MagicMock()
            mock_response.status_code = 404
            mock_client.get = AsyncMock(return_value=mock_response)

            with patch(
                "app.siftarr.services.overseerr_service.get_shared_client",
                return_value=mock_client,
            ):
                result = await service.get_request_status(999)

            assert result is None

    def test_clear_status_cache_empties_app_side_cache(self):
        """Clear helper should empty the in-memory Overseerr status cache."""
        overseerr_service._STATUS_CACHE.clear()
        overseerr_service._STATUS_CACHE.update(
            {
                1: (1.0, {"status": "approved"}),
                2: (2.0, {"status": "pending"}),
            }
        )

        cleared = overseerr_service.clear_status_cache()

        assert cleared == 2
        assert overseerr_service._STATUS_CACHE == {}
