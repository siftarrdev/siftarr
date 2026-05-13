"""Tests for background task helpers."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.exc import OperationalError

from app.siftarr.services.utils import background_tasks


def _sqlite_locked_error() -> OperationalError:
    return OperationalError("UPDATE seasons SET synced_at=?", {}, Exception("database is locked"))


@pytest.mark.asyncio
async def test_background_episode_refresh_retries_sqlite_locked_errors():
    db = AsyncMock()
    session_context = AsyncMock()
    session_context.__aenter__.return_value = db

    episode_sync = AsyncMock()
    episode_sync.sync_request = AsyncMock(side_effect=[_sqlite_locked_error(), None])

    with (
        patch.object(
            background_tasks.db_mod, "async_session_maker", MagicMock(return_value=session_context)
        ),
        patch(
            "app.siftarr.services.integrations.plex_service.PlexService", return_value=MagicMock()
        ),
        patch(
            "app.siftarr.services.lifecycle.episode_sync_service.EpisodeSyncService",
            return_value=episode_sync,
        ),
        patch.object(background_tasks, "SQLITE_LOCK_RETRY_DELAYS", (0,)),
        patch.object(background_tasks, "sleep", AsyncMock()),
    ):
        await background_tasks.run_background_episode_refresh(8)

    assert episode_sync.sync_request.await_count == 2
    db.rollback.assert_awaited_once()
    assert 8 not in background_tasks.DETAILS_SYNC_TASKS
