"""Settings job/action handlers."""

import sys

from fastapi import Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.siftarr.database import get_db
from app.siftarr.models.settings import Settings as DBSettings

from .shared import logger, router, templates

settings_router = sys.modules[__package__]


@router.post("/rescan-plex")
async def rescan_plex(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Run the legacy manual Plex reconcile path for existing requests."""
    context = await settings_router._build_settings_page_context(request, db)
    try:
        runtime_settings = await settings_router.get_effective_settings(db)
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
            "Legacy/manual Plex reconcile completed. "
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
    result = await db.execute(select(DBSettings).where(DBSettings.key == "staging_mode_enabled"))
    staging_setting = result.scalar_one_or_none()

    if staging_setting:
        staging_setting.value = "false" if staging_setting.value == "true" else "true"
    else:
        db.add(
            DBSettings(
                key="staging_mode_enabled",
                value="true",
                description="Enable staging mode to save torrents locally",
            )
        )

    await db.commit()
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


@router.post("/run-incremental-plex-sync")
async def run_incremental_plex_sync(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Manually trigger the incremental Plex sync scheduler job."""
    from app.siftarr.main import scheduler_service

    context = await settings_router._build_settings_page_context(request, db)
    if scheduler_service is None:
        context["message"] = "Scheduler not available"
        context["message_type"] = "error"
        return templates.TemplateResponse(request, "settings.html", context)

    result = await scheduler_service.trigger_incremental_plex_sync_now()
    context["message"], context["message_type"] = settings_router._build_manual_plex_job_message(
        "Incremental Plex sync",
        result,
    )
    context["plex_jobs"] = await settings_router._build_plex_job_statuses(db)
    return templates.TemplateResponse(request, "settings.html", context)


@router.post("/run-full-plex-reconcile")
async def run_full_plex_reconcile(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Manually trigger the full Plex reconcile scheduler job."""
    from app.siftarr.main import scheduler_service

    context = await settings_router._build_settings_page_context(request, db)
    if scheduler_service is None:
        context["message"] = "Scheduler not available"
        context["message_type"] = "error"
        return templates.TemplateResponse(request, "settings.html", context)

    result = await scheduler_service.trigger_full_plex_reconcile_now()
    context["message"], context["message_type"] = settings_router._build_manual_plex_job_message(
        "Full Plex reconcile",
        result,
    )
    context["plex_jobs"] = await settings_router._build_plex_job_statuses(db)
    return templates.TemplateResponse(request, "settings.html", context)
