"""Settings page handlers."""

import sys

from fastapi import Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.siftarr.database import get_db

from .shared import router, templates

settings_router = sys.modules[__package__]


@router.get("")
async def get_settings_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Display settings page."""
    rule_service = settings_router.RuleService(db)
    await rule_service.ensure_default_rules()
    context = await settings_router._build_settings_page_context(request, db)
    return templates.TemplateResponse(request, "settings.html", context)
