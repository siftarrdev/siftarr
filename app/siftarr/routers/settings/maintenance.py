"""Settings maintenance handlers."""

import sys

from fastapi import Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.siftarr.database import get_db

from .shared import logger, router, templates

settings_router = sys.modules[__package__ or "app.siftarr.routers.settings"]


@router.post("/clear-cache")
async def clear_cache(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Clear app-side persisted release results and Overseerr status cache."""
    context = await settings_router._build_settings_page_context(request, db)
    try:
        release_result = await settings_router.clear_release_search_cache(db)
        context["message"] = (
            "Cleared app search cache: "
            f"removed {release_result['deleted_releases']} stored release result(s) and "
            f"detached {release_result['detached_episode_refs']} episode link(s)."
        )
        context["message_type"] = "success"
    except Exception as exc:
        logger.exception("Failed to clear app search cache")
        await db.rollback()
        context["message"] = f"Failed to clear app search cache: {exc}"
        context["message_type"] = "error"
    return templates.TemplateResponse(request, "settings.html", context)


@router.post("/reseed-rules")
async def reseed_rules(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Reseed default rules."""
    rule_service = settings_router.RuleService(db)
    await rule_service.seed_default_rules()
    context = await settings_router._build_settings_page_context(request, db)
    context["message"] = "Default rules have been seeded"
    context["message_type"] = "success"
    return templates.TemplateResponse(request, "settings.html", context)
