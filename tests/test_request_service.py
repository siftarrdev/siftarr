"""Unit tests for app.siftarr.services.request_service."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.siftarr.models.request import MediaType, RequestStatus
from app.siftarr.models.request import Request as RequestModel
from app.siftarr.services.request_service import (
    bulk_redirect_url,
    ensure_tvdb_id,
    load_request_or_404,
    selection_redirect_url,
    validate_tv_request,
)


def _make_request(**overrides) -> MagicMock:
    """Create a mock Request with sensible defaults."""
    defaults = {
        "id": 1,
        "media_type": MediaType.TV,
        "status": RequestStatus.PENDING,
        "tvdb_id": 123,
    }
    defaults.update(overrides)
    req = MagicMock(spec=RequestModel)
    for key, value in defaults.items():
        setattr(req, key, value)
    return req


# -- validate_tv_request --------------------------------------------------------


class TestValidateTvRequest:
    def test_tv_request_passes(self) -> None:
        request = _make_request(media_type=MediaType.TV)
        validate_tv_request(request)  # should not raise

    def test_movie_request_raises_400(self) -> None:
        request = _make_request(media_type=MediaType.MOVIE)
        with pytest.raises(HTTPException) as exc_info:
            validate_tv_request(request)
        assert exc_info.value.status_code == 400
        assert "not a TV show" in exc_info.value.detail


# -- ensure_tvdb_id --------------------------------------------------------------


class TestEnsureTvdbId:
    def test_present_tvdb_id_passes_and_returns_id(self) -> None:
        request = _make_request(tvdb_id=123)
        result = ensure_tvdb_id(request)
        assert result == 123

    def test_none_tvdb_id_raises_400(self) -> None:
        request = _make_request(tvdb_id=None)
        with pytest.raises(HTTPException) as exc_info:
            ensure_tvdb_id(request)
        assert exc_info.value.status_code == 400
        assert "No TVDB ID" in exc_info.value.detail

    def test_zero_tvdb_id_raises_400(self) -> None:
        # tvdb_id = 0 is falsy and should also be rejected
        request = _make_request(tvdb_id=0)
        with pytest.raises(HTTPException) as exc_info:
            ensure_tvdb_id(request)
        assert exc_info.value.status_code == 400


# -- selection_redirect_url ------------------------------------------------------


class TestSelectionRedirectUrl:
    def test_returns_redirect_to_when_provided(self) -> None:
        request = _make_request(status=RequestStatus.PENDING)
        assert selection_redirect_url("/?tab=active", request) == "/?tab=active"

    def test_returns_pending_tab_for_pending_request(self) -> None:
        request = _make_request(status=RequestStatus.PENDING)
        assert selection_redirect_url(None, request) == "/?tab=pending"

    def test_returns_active_tab_for_non_pending_request(self) -> None:
        request = _make_request(status=RequestStatus.SEARCHING)
        assert selection_redirect_url(None, request) == "/?tab=active"

    def test_returns_active_tab_for_completed_request(self) -> None:
        request = _make_request(status=RequestStatus.COMPLETED)
        assert selection_redirect_url(None, request) == "/?tab=active"

    def test_redirect_to_takes_priority_over_status(self) -> None:
        request = _make_request(status=RequestStatus.SEARCHING)
        assert selection_redirect_url("/custom", request) == "/custom"

    def test_prefers_staged_tab_when_requested(self) -> None:
        request = _make_request(status=RequestStatus.PENDING)
        assert selection_redirect_url(None, request, prefer_staged_view=True) == "/?tab=staged"


# -- bulk_redirect_url ----------------------------------------------------------


class TestBulkRedirectUrl:
    def test_returns_redirect_to_when_provided(self) -> None:
        assert bulk_redirect_url("/?tab=active") == "/?tab=active"

    def test_defaults_to_pending_tab(self) -> None:
        assert bulk_redirect_url(None) == "/?tab=pending"

    def test_empty_string_defaults_to_pending(self) -> None:
        # empty string is falsy, so it falls through to the default
        assert bulk_redirect_url("") == "/?tab=pending"


# -- load_request_or_404 ---------------------------------------------------------


class TestLoadRequestOr404:
    @pytest.mark.asyncio
    async def test_returns_request_when_found(self) -> None:
        mock_request = _make_request()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = mock_request
        db = AsyncMock()
        db.execute.return_value = result_mock

        request = await load_request_or_404(db, 1)
        assert request is mock_request

    @pytest.mark.asyncio
    async def test_raises_404_when_not_found(self) -> None:
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        db = AsyncMock()
        db.execute.return_value = result_mock

        with pytest.raises(HTTPException) as exc_info:
            await load_request_or_404(db, 999)
        assert exc_info.value.status_code == 404
        assert "not found" in exc_info.value.detail.lower()
