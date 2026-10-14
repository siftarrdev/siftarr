"""Dashboard JSON API router for request details, search, and state mutations."""

import logging
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.siftarr.config import get_settings
from app.siftarr.database import get_db
from app.siftarr.models.request import MediaType
from app.siftarr.services.dashboard.dashboard_service import (
    serialize_request_details_response,
)
from app.siftarr.services.dashboard.detail_service import DetailService
from app.siftarr.services.integrations.plex_service import PlexService
from app.siftarr.services.request_service import load_request_or_404
from app.siftarr.services.search_history_service import SearchHistoryService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/requests", tags=["dashboard-api"])


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


@router.get("/{request_id}/details")
async def request_details(
    request_id: int,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    offset: int = 0,
    limit: int = 100,
    title: str | None = None,
    resolution: str | None = None,
    sort: str | None = None,
    direction: str | None = None,
) -> JSONResponse:
    request = await load_request_or_404(db, request_id)
    detail_service = DetailService(db, settings=get_settings())
    details = await detail_service.load_request_details(
        request,
        request_id=request_id,
        background_tasks=background_tasks,
        offset=offset,
        limit=limit,
        title=title,
        resolution=resolution,
        sort=sort,
        direction=direction,
    )
    return JSONResponse(serialize_request_details_response(details), background=background_tasks)


@router.get("/{request_id}/search-history")
async def request_search_history(
    request_id: int,
    db: AsyncSession = Depends(get_db),
    offset: int = 0,
    limit: int = 20,
    status: str | None = None,
    outcome: str | None = None,
    source: str | None = None,
    search_mode: str | None = None,
    started_after: str | None = None,
    started_before: str | None = None,
) -> JSONResponse:
    """Return paginated search history for one request."""
    await load_request_or_404(db, request_id)
    service = SearchHistoryService(db)
    payload = await service.list_runs(
        request_id,
        offset=offset,
        limit=limit,
        status=status,
        outcome=outcome,
        source=source,
        search_mode=search_mode,
        started_after=_parse_datetime(started_after),
        started_before=_parse_datetime(started_before),
    )
    return JSONResponse(payload)


@router.post("/{request_id}/refresh-plex")
async def refresh_plex(
    request_id: int,
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Run the simplified TV sync flow for one request."""
    from app.siftarr.services.lifecycle.episode_sync_service import EpisodeSyncService

    request = await load_request_or_404(db, request_id)

    if request.media_type != MediaType.TV:
        return JSONResponse({"error": "Request is not a TV show"})

    effective_settings = get_settings()
    plex_service = PlexService(settings=effective_settings)

    try:
        episode_sync = EpisodeSyncService(db, plex=plex_service)
        await episode_sync.sync_request(request_id)
        return JSONResponse({"status": "success", "message": "Plex sync completed"})
    except Exception:
        logger.exception("Plex refresh failed for request_id=%s", request_id)
        return JSONResponse({"status": "error", "message": "Plex sync failed"}, status_code=500)
