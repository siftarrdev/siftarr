"""Settings streaming/import handlers."""

import sys

from fastapi import Depends, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.siftarr.config import get_settings
from app.siftarr.database import get_db

from .shared import router, templates

settings_router = sys.modules[__package__ or "app.siftarr.routers.settings"]


@router.get("/api/rescan-plex/stream")
async def rescan_plex_stream(shallow: bool = False) -> StreamingResponse:
    """Stream Plex re-scan progress via SSE."""
    return StreamingResponse(
        settings_router._rescan_plex_generator(shallow=shallow),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/api/sync-overseerr/stream")
async def sync_overseerr_stream() -> StreamingResponse:
    """Stream Overseerr sync progress via SSE."""
    return StreamingResponse(
        settings_router._sync_overseerr_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/sync-overseerr")
async def sync_overseerr(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Sync with Overseerr for new requests."""
    context = await settings_router._build_settings_page_context(request, db)
    effective_settings = context["env"]

    if not effective_settings.get("overseerr_url") or not effective_settings.get(
        "overseerr_api_key"
    ):
        context["message"] = "Overseerr is not configured. Please set URL and API key."
        context["message_type"] = "error"
        return templates.TemplateResponse(request, "settings.html", context)

    try:
        runtime_settings = get_settings()
        synced_count, skipped_count = await settings_router._import_overseerr_requests(
            db,
            runtime_settings,
        )
        if synced_count > 0:
            context["message"] = f"Synced {synced_count} new request(s) from Overseerr"
        elif synced_count == 0 and skipped_count == 0:
            context["message"] = "No requests found in Overseerr"
        else:
            context["message"] = (
                "No new actionable requests to sync "
                f"({skipped_count} already existed or were already available)"
            )
        context["message_type"] = "success"
    except Exception as exc:
        context["message"] = f"Sync error: {exc}"
        context["message_type"] = "error"

    return templates.TemplateResponse(request, "settings.html", context)
