"""Settings job/action handlers."""

import sys

from fastapi import Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.siftarr.config import get_settings
from app.siftarr.database import get_db

from .shared import logger, router, templates

settings_router = sys.modules[__package__ or "app.siftarr.routers.settings"]


@router.post("/rescan-plex")
async def rescan_plex(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Run the manual Plex rescan path for existing requests."""
    context = await settings_router._build_settings_page_context(request, db)
    try:
        runtime_settings = get_settings()
        plex = settings_router.PlexService(settings=runtime_settings)
        try:
            tv_resynced, tv_failed, completed = await settings_router._rescan_plex_requests(
                db,
                runtime_settings,
                plex,
            )
        finally:
            await plex.close()

        context["message"] = (
            "Manual Plex rescan completed. "
            f"Re-synced {tv_resynced} TV request(s), had {tv_failed} failed TV request(s), "
            f"and transitioned {completed} request(s) to completed."
        )
        context["message_type"] = "success"
    except Exception as exc:
        logger.exception("Plex availability re-scan failed")
        context["message"] = f"Plex availability re-scan failed: {exc}"
        context["message_type"] = "error"
    return templates.TemplateResponse(request, "settings.html", context)


@router.post("/staging")
async def toggle_staging_mode(db: AsyncSession = Depends(get_db)) -> RedirectResponse:
    """Toggle staging mode."""
    del db
    staging_enabled = get_settings().staging_mode_enabled
    await settings_router._set_db_setting(
        None,
        "staging_mode_enabled",
        "false" if staging_enabled else "true",
    )
    return RedirectResponse(url="/settings", status_code=303)


@router.post("/retry-pending")
async def retry_pending(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Manually trigger retry of pending items."""
    from app.siftarr.main import scheduler_service

    context = await settings_router._build_settings_page_context(request, db)
    if scheduler_service:
        count = await scheduler_service.trigger_retry_now()
        context["message"] = f"Retrying {count} pending items"
        context["message_type"] = "success"
    else:
        context["message"] = "Scheduler not available"
        context["message_type"] = "error"
    return templates.TemplateResponse(request, "settings.html", context)


@router.post("/run-recent-plex-scan")
async def run_recent_plex_scan(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Manually trigger the recent Plex scan scheduler job."""
    from app.siftarr.main import scheduler_service

    context = await settings_router._build_settings_page_context(request, db)
    if scheduler_service is None:
        context["message"] = "Scheduler not available"
        context["message_type"] = "error"
        return templates.TemplateResponse(request, "settings.html", context)

    result = await scheduler_service.trigger_recent_plex_scan_now()
    context["message"], context["message_type"] = settings_router._build_manual_plex_job_message(
        "Recent Plex scan",
        result,
    )
    context["plex_jobs"] = await settings_router._build_plex_job_statuses(db)
    return templates.TemplateResponse(request, "settings.html", context)


@router.post("/run-plex-poll")
async def run_plex_poll(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Manually trigger the Plex poll scheduler job."""
    from app.siftarr.main import scheduler_service

    context = await settings_router._build_settings_page_context(request, db)
    if scheduler_service is None:
        context["message"] = "Scheduler not available"
        context["message_type"] = "error"
        return templates.TemplateResponse(request, "settings.html", context)

    result = await scheduler_service.trigger_plex_poll_now()
    context["message"], context["message_type"] = settings_router._build_manual_plex_job_message(
        "Plex poll",
        result,
    )
    context["plex_jobs"] = await settings_router._build_plex_job_statuses(db)
    return templates.TemplateResponse(request, "settings.html", context)
