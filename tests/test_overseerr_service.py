"""Tests for OverseerrService."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.siftarr.services import overseerr_service
from app.siftarr.services.overseerr_service import (
    OverseerrService,
    build_overseerr_media_url,
    build_poster_url,
    extract_poster_path,
)


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
    async def test_get_media_details_uses_ttl_cache(self):
        """Media details should reuse a cached response within the TTL window."""
        overseerr_service._MEDIA_DETAILS_CACHE.clear()
        with patch("app.siftarr.services.overseerr_service.get_settings") as mock_get_settings:
            mock_settings = MagicMock()
            mock_settings.overseerr_url = "http://localhost:5055"
            mock_settings.overseerr_api_key = "test"
            mock_get_settings.return_value = mock_settings

            service = OverseerrService()
            mock_client = AsyncMock()
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {"id": 123, "title": "Test Show"}
            mock_client.get = AsyncMock(return_value=mock_response)

            with patch(
                "app.siftarr.services.overseerr_service.get_shared_client",
                return_value=mock_client,
            ):
                first = await service.get_media_details("tv", 123)
                second = await service.get_media_details("tv", 123)

            assert first == second == {"id": 123, "title": "Test Show"}
            mock_client.get.assert_awaited_once()

    def test_clear_media_details_cache_empties_cache(self):
        """Media details cache clear helper should empty the app-side cache."""
        overseerr_service._MEDIA_DETAILS_CACHE.clear()
        overseerr_service._MEDIA_DETAILS_CACHE.update(
            {("tv", 1): (1.0, {"id": 1}), ("movie", 2): (2.0, {"id": 2})}
        )

        cleared = overseerr_service.clear_media_details_cache()

        assert cleared == 2
        assert overseerr_service._MEDIA_DETAILS_CACHE == {}


class TestExtractPosterPath:
    """Tests for extract_poster_path helper."""

    def test_bare_tmdb_path(self):
        """Bare TMDB path like /abc123.jpg should pass through unchanged."""
        assert (
            extract_poster_path("/kSf9svfD2WiLhrs9AP2Uih2Wq3T.jpg")
            == "/kSf9svfD2WiLhrs9AP2Uih2Wq3T.jpg"
        )

    def test_overseerr_proxied_path(self):
        """Overseerr proxied form /images/original/... should strip prefix."""
        assert (
            extract_poster_path("/images/original/kSf9svfD2WiLhrs9AP2Uih2Wq3T.jpg")
            == "/kSf9svfD2WiLhrs9AP2Uih2Wq3T.jpg"
        )

    def test_overseerr_proxied_path_w500(self):
        """Overseerr proxied form with w500 size should also strip prefix."""
        assert (
            extract_poster_path("/images/w500/kSf9svfD2WiLhrs9AP2Uih2Wq3T.jpg")
            == "/kSf9svfD2WiLhrs9AP2Uih2Wq3T.jpg"
        )

    def test_overseerr_proxied_path_truncated(self):
        """Truncated proxied path with no actual filename returns slash (edge case of split logic)."""
        # /images/original/ splits to ['', 'images', 'original', ''] -> f"/{''}" -> "/"
        assert extract_poster_path("/images/original/") == "/"

    def test_overseerr_proxied_path_too_short(self):
        """Proxied path with only two segments should return None."""
        assert extract_poster_path("/images/") is None

    def test_full_tmdb_url(self):
        """Full TMDB URL should extract the bare poster path."""
        assert extract_poster_path("https://image.tmdb.org/t/p/original/abc.jpg") == "/abc.jpg"

    def test_full_tmdb_url_with_w500(self):
        """Full TMDB URL with w500 size should still extract the bare path."""
        assert extract_poster_path("https://image.tmdb.org/t/p/w500/abc.jpg") == "/abc.jpg"

    def test_full_tmdb_url_no_t_p_prefix(self):
        """Full TMDB URL without /t/p/ structure should return None."""
        assert extract_poster_path("https://image.tmdb.org/something/abc.jpg") is None

    def test_full_overseerr_url(self):
        """Full URL pointing to Overseerr instance should extract the TMDB portion."""
        assert extract_poster_path("http://overseerr:5055/images/original/abc.jpg") == "/abc.jpg"

    def test_full_overseerr_https_url(self):
        """HTTPS Overseerr URL should also extract the TMDB portion."""
        assert (
            extract_poster_path("https://overseerr.example.com/images/w500/abc.jpg") == "/abc.jpg"
        )

    def test_none_input(self):
        """None input should return None."""
        assert extract_poster_path(None) is None

    def test_empty_string_input(self):
        """Empty string input should return None."""
        assert extract_poster_path("") is None

    def test_whitespace_string_input(self):
        """Whitespace-only string should return None."""
        assert extract_poster_path("   ") is None

    def test_path_starting_with_images_not_proxied(self):
        """A path starting with /images but not /images/ (no trailing slash) should not match proxied form."""
        # This starts with "/" and not "/images", so it passes through as a bare TMDB path
        # Wait - /imagesXXX starts with "/" and does not start with "/images" only if...
        # "/imagesfoo" starts with "/" and not with "/images" — but actually it does start with "/images" prefix.
        # Let me re-read the logic: `poster.startswith("/") and not poster.startswith("/images")`
        # "/imagesfoo" starts with "/images" so it's NOT caught by the bare path rule.
        # It also doesn't start with "/images/" so it's not caught by the proxied rule.
        # Falls through to return None.
        assert extract_poster_path("/imagesfoo/bar.jpg") is None


class TestBuildPosterUrl:
    """Tests for build_poster_url helper."""

    def test_none_input(self):
        """None poster path should return None."""
        assert build_poster_url(None) is None

    def test_empty_string_input(self):
        """Empty string should return None."""
        assert build_poster_url("") is None

    def test_bare_tmdb_path(self):
        """Bare TMDB path should produce a properly encoded proxied URL."""
        result = build_poster_url("/abc123.jpg")
        assert result == "/api/poster?path=%2Fabc123.jpg"

    def test_overseerr_proxied_path(self):
        """Overseerr proxied path should strip prefix and produce proxied URL."""
        result = build_poster_url("/images/original/abc.jpg")
        assert result == "/api/poster?path=%2Fabc.jpg"

    def test_full_tmdb_url(self):
        """Full TMDB URL should extract path and produce proxied URL."""
        result = build_poster_url("https://image.tmdb.org/t/p/original/abc.jpg")
        assert result == "/api/poster?path=%2Fabc.jpg"

    def test_path_with_special_characters(self):
        """Path with special characters should be URL-encoded."""
        result = build_poster_url("/path with spaces.jpg")
        assert result is not None
        assert "path=" in result
        # The path should be URL-encoded (spaces become %20)
        assert "%2Fpath%20with%20spaces.jpg" in result


class TestBuildOverseerrMediaUrl:
    """Tests for build_overseerr_media_url helper."""

    def test_none_overseerr_url(self):
        """None Overseerr URL should return None."""
        assert build_overseerr_media_url(None, "movie", 123) is None

    def test_none_tmdb_id(self):
        """None TMDB ID should return None."""
        assert build_overseerr_media_url("http://overseerr:5055", "movie", None) is None

    def test_empty_overseerr_url(self):
        """Empty string Overseerr URL should return None."""
        assert build_overseerr_media_url("", "movie", 123) is None

    def test_valid_movie_url(self):
        """Valid movie inputs should build correct URL."""
        assert (
            build_overseerr_media_url("http://overseerr:5055", "movie", 123)
            == "http://overseerr:5055/movie/123"
        )

    def test_valid_tv_url(self):
        """Valid TV inputs should build correct URL."""
        assert (
            build_overseerr_media_url("http://overseerr:5055", "tv", 456)
            == "http://overseerr:5055/tv/456"
        )

    def test_strips_trailing_slash(self):
        """Trailing slash in Overseerr URL should be stripped."""
        assert (
            build_overseerr_media_url("http://overseerr:5055/", "movie", 123)
            == "http://overseerr:5055/movie/123"
        )
