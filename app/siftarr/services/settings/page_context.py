"""Settings page context helpers."""

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.siftarr.config import Settings


async def set_db_setting(
    db: AsyncSession,
    key: str,
    value: str,
    description: str | None = None,
    *,
    settings_model,
) -> None:
    """Set a setting value in the database."""
    result = await db.execute(select(settings_model).where(settings_model.key == key))
    setting = result.scalar_one_or_none()
    if setting:
        setting.value = value
    else:
        setting = settings_model(key=key, value=value, description=description)
        db.add(setting)


async def build_effective_settings(db: AsyncSession, *, get_effective_settings_func) -> dict[str, Any]:
    """Build effective settings using DB overrides."""
    effective = await get_effective_settings_func(db)
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


async def build_effective_settings_obj(
    db: AsyncSession, *, build_effective_settings_func
) -> Settings:
    """Build effective Settings object from flattened settings values."""
    effective = await build_effective_settings_func(db)
    return Settings(
        overseerr_url=effective["overseerr_url"] or None,
        overseerr_api_key=effective["overseerr_api_key"] or None,
        prowlarr_url=effective["prowlarr_url"] or None,
        prowlarr_api_key=effective["prowlarr_api_key"] or None,
        qbittorrent_url=effective["qbittorrent_url"] or None,
        qbittorrent_username=effective["qbittorrent_username"],
        qbittorrent_password=effective["qbittorrent_password"],
        plex_url=effective["plex_url"] or None,
        plex_token=effective["plex_token"] or None,
        tz=effective["tz"],
    )


async def build_settings_page_context(
    request,
    db: AsyncSession,
    *,
    build_effective_settings_func,
    settings_model,
    pending_queue_service_cls,
    request_model,
    request_status_enum,
    build_plex_job_statuses_func,
) -> dict[str, Any]:
    """Build the shared context required by the settings page."""
    effective_settings = await build_effective_settings_func(db)

    result = await db.execute(select(settings_model).where(settings_model.key == "staging_mode_enabled"))
    staging_setting = result.scalar_one_or_none()
    staging_enabled = staging_setting.value == "true" if staging_setting else True

    queue_service = pending_queue_service_cls(db)
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
