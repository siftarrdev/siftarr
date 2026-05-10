"""Shared fixtures for settings router tests."""

from collections.abc import Callable
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.fixture
def mock_db() -> AsyncMock:
    """Return a reusable async DB mock."""

    return AsyncMock()


@pytest.fixture(autouse=True)
def mock_settings_store_get(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure SettingsStore.get returns None for SSO lookups in all tests."""

    async def fake_get(self, key: str) -> str | None:
        # Only intercept known DB-backed keys that don't exist in mocks
        if key in ("plex_username", "plex_thumb", "plex_claimed_id"):
            return None
        return None

    monkeypatch.setattr("app.siftarr.services.admin.settings_service.SettingsStore.get", fake_get)


@pytest.fixture
def base_context() -> Callable[[], dict[str, Any]]:
    """Build the default settings page context."""

    def factory() -> dict[str, Any]:
        return {
            "request": MagicMock(),
            "env": {
                "overseerr_url": "",
                "overseerr_api_key": "",
                "prowlarr_url": "",
                "prowlarr_api_key": "",
                "qbittorrent_url": "",
                "qbittorrent_api_key": "",
                "plex_url": "",
                "plex_token": "",
                "tz": "UTC",
            },
            "staging_enabled": True,
            "pending_count": 0,
            "stats": {"total_requests": 0, "completed": 0, "pending": 0, "failed": 0},
            "plex_jobs": [],
        }

    return factory
