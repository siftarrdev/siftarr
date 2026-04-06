"""Dashboard router for main UI."""

from fastapi import APIRouter, Depends, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.arbitratarr.database import get_db
from app.arbitratarr.models.request import RequestStatus
from app.arbitratarr.models.staged_torrent import StagedTorrent
from app.arbitratarr.services.lifecycle_service import LifecycleService
from app.arbitratarr.services.pending_queue_service import PendingQueueService

router = APIRouter(tags=["dashboard"])
templates = Jinja2Templates(directory="app/arbitratarr/templates")


@router.get("/")
async def dashboard(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Display main dashboard."""
    lifecycle_service = LifecycleService(db)
    queue_service = PendingQueueService(db)

    # Get active requests
    active_requests = await lifecycle_service.get_active_requests(limit=50)

    # Get pending items
    pending_items = await queue_service.get_all_pending()

    # Get staged torrents
    result = await db.execute(
        select(StagedTorrent)
        .where(StagedTorrent.status == "staged")
        .order_by(StagedTorrent.created_at.desc())
    )
    staged_torrents = list(result.scalars().all())

    # Get stats
    stats = await lifecycle_service.get_requests_stats()

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "request": request,
            "active_requests": active_requests,
            "pending_items": pending_items,
            "staged_torrents": staged_torrents,
            "stats": {
                "active": sum(
                    1
                    for s in active_requests
                    if s.status
                    not in [
                        RequestStatus.PENDING,
                        RequestStatus.COMPLETED,
                        RequestStatus.FAILED,
                    ]
                ),
                "pending": stats["by_status"].get(RequestStatus.PENDING.value, 0),
                "staged": len(staged_torrents),
                "completed": stats["by_status"].get(RequestStatus.COMPLETED.value, 0),
            },
        },
    )
