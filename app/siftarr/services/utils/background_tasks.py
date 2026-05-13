"""Background task management for episode refresh operations."""

import logging
from asyncio import sleep

from fastapi import BackgroundTasks
from sqlalchemy.exc import OperationalError

from app.siftarr import database as db_mod
from app.siftarr.config import get_settings
from app.siftarr.database import init_engine

logger = logging.getLogger(__name__)

# Module-level mutable state for tracking active refresh tasks
DETAILS_SYNC_TASKS: set[int] = set()
SQLITE_LOCK_RETRY_DELAYS = (1.0, 2.0, 5.0)


def _is_sqlite_locked_error(exc: OperationalError) -> bool:
    return "database is locked" in str(exc).lower()


async def run_background_episode_refresh(request_id: int) -> None:
    """Refresh TV details in a detached task using a fresh DB session."""
    if request_id not in DETAILS_SYNC_TASKS:
        DETAILS_SYNC_TASKS.add(request_id)
    try:
        if db_mod.async_session_maker is None:
            init_engine()
        assert db_mod.async_session_maker is not None
        async with db_mod.async_session_maker() as db:
            effective_settings = get_settings()
            plex_service = None
            try:
                from app.siftarr.services.integrations.plex_service import PlexService
                from app.siftarr.services.lifecycle.episode_sync_service import EpisodeSyncService

                plex_service = PlexService(settings=effective_settings)
                episode_sync = EpisodeSyncService(db, plex=plex_service)
                for attempt in range(len(SQLITE_LOCK_RETRY_DELAYS) + 1):
                    try:
                        await episode_sync.sync_request(request_id)
                        break
                    except OperationalError as exc:
                        if not _is_sqlite_locked_error(exc):
                            raise
                        await db.rollback()
                        if attempt == len(SQLITE_LOCK_RETRY_DELAYS):
                            logger.warning(
                                "Background episode sync skipped after SQLite lock retries: request_id=%s attempts=%s",
                                request_id,
                                attempt + 1,
                            )
                            break
                        delay = SQLITE_LOCK_RETRY_DELAYS[attempt]
                        logger.info(
                            "Background episode sync hit SQLite lock; retrying: request_id=%s attempt=%s delay=%.1fs",
                            request_id,
                            attempt + 1,
                            delay,
                        )
                        await sleep(delay)
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
