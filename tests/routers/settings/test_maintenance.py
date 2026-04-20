"""Settings maintenance action tests."""

from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.siftarr.routers import settings


@pytest.mark.asyncio
async def test_clear_cache_route_reports_success(monkeypatch, mock_db, base_context):
    """Clear-cache action should report what was removed from app-side caches."""

    monkeypatch.setattr(
        settings,
        "_build_settings_page_context",
        AsyncMock(return_value=base_context()),
    )
    monkeypatch.setattr(
        settings,
        "clear_release_search_cache",
        AsyncMock(return_value={"deleted_releases": 4, "detached_episode_refs": 2}),
    )

    response = await settings.clear_cache(MagicMock(), db=mock_db)
    context = cast(dict, getattr(response, "context", None))

    assert context["message_type"] == "success"
    assert "removed 4 stored release result(s)" in context["message"]
    assert "detached 2 episode link(s)" in context["message"]


@pytest.mark.asyncio
async def test_clear_cache_route_reports_failure_and_rolls_back(monkeypatch, mock_db, base_context):
    """Clear-cache errors should be surfaced without leaving the transaction open."""

    monkeypatch.setattr(
        settings,
        "_build_settings_page_context",
        AsyncMock(return_value=base_context()),
    )
    monkeypatch.setattr(
        settings,
        "clear_release_search_cache",
        AsyncMock(side_effect=RuntimeError("boom")),
    )

    response = await settings.clear_cache(MagicMock(), db=mock_db)
    context = cast(dict, getattr(response, "context", None))

    assert context["message_type"] == "error"
    assert context["message"] == "Failed to clear app search cache: boom"
    mock_db.rollback.assert_awaited_once()
