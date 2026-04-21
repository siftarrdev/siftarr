"""Settings page context helpers."""

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.siftarr.config import Settings, get_settings
from app.siftarr.services.pending_queue_service import PendingQueueService


async def build_effective_settings() -> dict[str, Any]:
    """Build the effective flattened settings payload."""
    effective = get_settings()
    return {
        "overseerr_url": str(effective.overseerr_url or ""),
        "overseerr_api_key": str(effective.overseerr_api_key or ""),
        "prowlarr_url": str(effective.prowlarr_url or ""),
        "prowlarr_api_key": str(effective.prowlarr_api_key or ""),
        "qbittorrent_url": str(effective.qbittorrent_url or ""),
        "qbittorrent_username": effective.qbittorrent_username,
        "qbittorrent_password": effective.qbittorrent_password,
        "plex_url": str(effective.plex_url or ""),
        "plex_token": effective.plex_token or "",
        "tz": effective.tz,
    }


async def build_effective_settings_obj(db: AsyncSession) -> Settings:
    """Build the effective Settings object."""
    del db
    return get_settings()


async def build_settings_page_context(
    request,
    db: AsyncSession,
    *,
    request_model,
    request_status_enum,
    build_plex_job_statuses_func,
) -> dict[str, Any]:
    """Build the shared context required by the settings page."""
    effective_settings = await build_effective_settings()

    staging_enabled = get_settings().staging_mode_enabled

    queue_service = PendingQueueService(db)
    pending_count = len(await queue_service.get_ready_for_retry())

    status_counts = (
        await db.execute(select(request_model.status, func.count()).group_by(request_model.status))
    ).all()
    stats_by_status = {status.value: count for status, count in status_counts}

    return {
        "request": request,
        "staging_enabled": staging_enabled,
        "pending_count": pending_count,
        "stats": {
            "total_requests": sum(stats_by_status.values()),
            "completed": stats_by_status.get(request_status_enum.COMPLETED.value, 0),
            "pending": stats_by_status.get(request_status_enum.PENDING.value, 0),
            "failed": stats_by_status.get(request_status_enum.FAILED.value, 0),
        },
        "plex_jobs": await build_plex_job_statuses_func(db),
        "env": effective_settings,
    }
