"""Shared fixtures for settings router tests."""

from collections.abc import Callable
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.fixture
def mock_db() -> AsyncMock:
    """Return a reusable async DB mock."""

    return AsyncMock()


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
                "qbittorrent_username": "",
                "qbittorrent_password": "",
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
