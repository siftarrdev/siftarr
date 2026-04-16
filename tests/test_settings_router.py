"""Tests for settings router cache-clearing behavior."""

from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.siftarr.routers import settings


class TestSettingsRouter:
    """Focused tests for settings manual actions."""

    @pytest.mark.asyncio
    async def test_get_settings_page_includes_clear_cache_scope_copy(self, monkeypatch):
        """Settings page should describe the app-side cache-clearing scope and limits."""
        mock_db = AsyncMock()
        rule_service = MagicMock()
        rule_service.ensure_default_rules = AsyncMock()

        monkeypatch.setattr(settings, "RuleService", lambda db: rule_service)
        monkeypatch.setattr(
            settings,
            "_build_settings_page_context",
            AsyncMock(
                return_value={
                    "request": MagicMock(),
                    "env": {},
                    "staging_enabled": True,
                    "pending_count": 0,
                    "stats": {"total_requests": 0, "completed": 0, "pending": 0, "failed": 0},
                }
            ),
        )

        response = await settings.get_settings_page(MagicMock(), db=mock_db)
        body = cast(bytes, response.body).decode()

        assert "Clear App Search Cache" in body
        assert "releases table" in body
        assert "Overseerr status cache" in body
        assert "external/manual Prowlarr caching cannot be guaranteed" in body

    @pytest.mark.asyncio
    async def test_clear_cache_route_reports_success(self, monkeypatch):
        """Clear-cache action should report what was removed from app-side caches."""
        mock_db = AsyncMock()
        base_context = {
            "request": MagicMock(),
            "env": {},
            "staging_enabled": True,
            "pending_count": 0,
            "stats": {"total_requests": 0, "completed": 0, "pending": 0, "failed": 0},
        }

        monkeypatch.setattr(
            settings,
            "_build_settings_page_context",
            AsyncMock(return_value=base_context.copy()),
        )
        monkeypatch.setattr(
            settings,
            "clear_release_search_cache",
            AsyncMock(return_value={"deleted_releases": 4, "detached_episode_refs": 2}),
        )
        monkeypatch.setattr(settings, "clear_status_cache", MagicMock(return_value=3))

        response = await settings.clear_cache(MagicMock(), db=mock_db)
        context = cast(dict, getattr(response, "context", None))

        assert context["message_type"] == "success"
        assert "removed 4 stored release result(s)" in context["message"]
        assert "detached 2 episode link(s)" in context["message"]
        assert "cleared 3 Overseerr status cache entries" in context["message"]

    @pytest.mark.asyncio
    async def test_clear_cache_route_reports_failure_and_rolls_back(self, monkeypatch):
        """Clear-cache errors should be surfaced without leaving the transaction open."""
        mock_db = AsyncMock()
        base_context = {
            "request": MagicMock(),
            "env": {},
            "staging_enabled": True,
            "pending_count": 0,
            "stats": {"total_requests": 0, "completed": 0, "pending": 0, "failed": 0},
        }

        monkeypatch.setattr(
            settings,
            "_build_settings_page_context",
            AsyncMock(return_value=base_context.copy()),
        )
        monkeypatch.setattr(
            settings,
            "clear_release_search_cache",
            AsyncMock(side_effect=RuntimeError("boom")),
        )
        clear_status_cache = MagicMock()
        monkeypatch.setattr(settings, "clear_status_cache", clear_status_cache)

        response = await settings.clear_cache(MagicMock(), db=mock_db)
        context = cast(dict, getattr(response, "context", None))

        assert context["message_type"] == "error"
        assert context["message"] == "Failed to clear app search cache: boom"
        mock_db.rollback.assert_awaited_once()
        clear_status_cache.assert_not_called()

    @pytest.mark.asyncio
    async def test_settings_page_includes_reseed_default_snapshot_copy(self, monkeypatch):
        """Settings copy should describe reseeding the checked-in 12-rule snapshot."""
        mock_db = AsyncMock()
        rule_service = MagicMock()
        rule_service.ensure_default_rules = AsyncMock()

        monkeypatch.setattr(settings, "RuleService", lambda db: rule_service)
        monkeypatch.setattr(
            settings,
            "_build_settings_page_context",
            AsyncMock(
                return_value={
                    "request": MagicMock(),
                    "env": {},
                    "staging_enabled": True,
                    "pending_count": 0,
                    "stats": {"total_requests": 0, "completed": 0, "pending": 0, "failed": 0},
                }
            ),
        )

        response = await settings.get_settings_page(MagicMock(), db=mock_db)
        body = cast(bytes, response.body).decode()

        assert "checked-in 12-rule default snapshot" in body
