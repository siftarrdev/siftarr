"""Unit tests for app.siftarr.services.request_service."""

from unittest.mock import MagicMock

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
from tests.factories import make_request


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


def test_validate_tv_request_accepts_tv_and_rejects_movie() -> None:
    validate_tv_request(_make_request(media_type=MediaType.TV))

    with pytest.raises(HTTPException) as exc_info:
        validate_tv_request(_make_request(media_type=MediaType.MOVIE))
    assert exc_info.value.status_code == 400
    assert "not a TV show" in exc_info.value.detail


def test_ensure_tvdb_id_returns_id() -> None:
    assert ensure_tvdb_id(_make_request(tvdb_id=123)) == 123


@pytest.mark.parametrize("tvdb_id", [None, 0], ids=["none", "zero"])
def test_ensure_tvdb_id_rejects_missing_id(tvdb_id) -> None:
    with pytest.raises(HTTPException) as exc_info:
        ensure_tvdb_id(_make_request(tvdb_id=tvdb_id))
    assert exc_info.value.status_code == 400
    assert "No TVDB ID" in exc_info.value.detail


@pytest.mark.parametrize(
    ("redirect_to", "status", "prefer_staged", "expected"),
    [
        ("/?tab=active", RequestStatus.PENDING, False, "/?tab=active"),
        ("/custom", RequestStatus.SEARCHING, False, "/custom"),
        (None, RequestStatus.PENDING, False, "/?tab=pending"),
        (None, RequestStatus.SEARCHING, False, "/?tab=active"),
        (None, RequestStatus.COMPLETED, False, "/?tab=active"),
        (None, RequestStatus.PENDING, True, "/?tab=staged"),
    ],
    ids=[
        "explicit-redirect",
        "explicit-redirect-beats-status",
        "pending",
        "searching",
        "completed",
        "prefer-staged",
    ],
)
def test_selection_redirect_url(redirect_to, status, prefer_staged, expected) -> None:
    request = _make_request(status=status)
    result = selection_redirect_url(redirect_to, request, prefer_staged_view=prefer_staged)
    assert result == expected


@pytest.mark.parametrize(
    ("redirect_to", "expected"),
    [("/?tab=active", "/?tab=active"), (None, "/?tab=pending"), ("", "/?tab=pending")],
    ids=["explicit", "none-defaults-to-pending", "empty-defaults-to-pending"],
)
def test_bulk_redirect_url(redirect_to, expected) -> None:
    assert bulk_redirect_url(redirect_to) == expected


@pytest.mark.asyncio
async def test_load_request_or_404(db) -> None:
    stored = await make_request(db)

    assert (await load_request_or_404(db, stored.id)).id == stored.id
    with pytest.raises(HTTPException) as exc_info:
        await load_request_or_404(db, stored.id + 999)
    assert exc_info.value.status_code == 404
    assert "not found" in exc_info.value.detail.lower()
