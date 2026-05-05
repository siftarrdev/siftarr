"""Background task management for episode refresh operations."""

import logging

from fastapi import BackgroundTasks

from app.siftarr.config import get_settings
from app.siftarr.database import async_session_maker, init_engine

logger = logging.getLogger(__name__)

# Module-level mutable state for tracking active refresh tasks
DETAILS_SYNC_TASKS: set[int] = set()


async def run_background_episode_refresh(request_id: int) -> None:
    """Refresh TV details in a detached task using a fresh DB session."""
    if request_id not in DETAILS_SYNC_TASKS:
        DETAILS_SYNC_TASKS.add(request_id)
    try:
        if async_session_maker is None:
            init_engine()
        assert async_session_maker is not None
        async with async_session_maker() as db:
            effective_settings = get_settings()
            plex_service = None
            try:
                from app.siftarr.services.episode_sync_service import EpisodeSyncService
                from app.siftarr.services.plex_service import PlexService

                plex_service = PlexService(settings=effective_settings)
                episode_sync = EpisodeSyncService(db, plex=plex_service)
                await episode_sync.sync_request(request_id)
            except Exception:
                logger.exception("Background episode sync failed for request_id=%s", request_id)
    finally:
        DETAILS_SYNC_TASKS.discard(request_id)


def schedule_background_episode_refresh(
    background_tasks: BackgroundTasks | None,
    request_id: int,
) -> bool:
    """Schedule a lifecycle-managed background refresh once per request."""
    if background_tasks is None:
        return False
    if request_id in DETAILS_SYNC_TASKS:
        return False

    DETAILS_SYNC_TASKS.add(request_id)
    background_tasks.add_task(run_background_episode_refresh, request_id)
    return True
