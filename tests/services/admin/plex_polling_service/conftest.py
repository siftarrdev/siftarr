from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.siftarr.services.admin.plex_polling_service import PlexPollingService


@pytest.fixture
def mock_plex():
    plex = AsyncMock()
    plex.settings = SimpleNamespace(
        plex_sync_concurrency=16,
    )

    @asynccontextmanager
    async def scan_cycle():
        yield plex

    plex.scan_cycle = scan_cycle
    return plex


@pytest.fixture
def service(db, mock_plex):
    """Polling service backed by the shared in-memory database and a mocked Plex client."""
    return PlexPollingService(db, mock_plex)
