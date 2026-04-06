"""Dashboard router for main UI."""

from fastapi import APIRouter, Depends, HTTPException
from fastapi import Request as FastAPIRequest
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.arbitratarr.database import get_db
from app.arbitratarr.models.request import Request as RequestModel
from app.arbitratarr.models.request import RequestStatus
from app.arbitratarr.models.staged_torrent import StagedTorrent
from app.arbitratarr.services.lifecycle_service import LifecycleService
from app.arbitratarr.services.overseerr_service import OverseerrService
from app.arbitratarr.services.pending_queue_service import PendingQueueService

router = APIRouter(tags=["dashboard"])
templates = Jinja2Templates(directory="app/arbitratarr/templates")


@router.get("/")
async def dashboard(
    request: FastAPIRequest,
    db: AsyncSession = Depends(get_db),
):
    """Display main dashboard."""
    lifecycle_service = LifecycleService(db)
    queue_service = PendingQueueService(db)
    overseerr_service = OverseerrService()

    # Get active requests
    active_requests = await lifecycle_service.get_active_requests(limit=100)

    # Fetch Overseerr statuses for requests that have overseerr_request_id
    overseerr_statuses: dict[int, str] = {}
    for req in active_requests:
        if req.overseerr_request_id:
            try:
                ov_status = await overseerr_service.get_request_status(req.overseerr_request_id)
                if ov_status:
                    media = ov_status.get("media") or {}
                    overseerr_statuses[req.id] = media.get("status", "unknown")
            except Exception:
                overseerr_statuses[req.id] = "unknown"

    await overseerr_service.close()

    # Filter active requests to only show Overseerr statuses we care about
    # These are the statuses that indicate the request needs action
    SHOW_STATUSES = {"pending", "requested", "partially_available"}
    filtered_requests = [
        req for req in active_requests
        if overseerr_statuses.get(req.id, "") in SHOW_STATUSES
        or overseerr_statuses.get(req.id, "") == ""  # Could not fetch status, show anyway
        or not req.overseerr_request_id  # Include requests without Overseerr ID
    ]

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
            "active_requests": filtered_requests,
            "overseerr_statuses": overseerr_statuses,
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


@router.post("/requests/{request_id}/approve")
async def approve_request(
    request_id: int,
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    """Approve a request in Overseerr and trigger search."""
    result = await db.execute(select(RequestModel).where(RequestModel.id == request_id))
    request = result.scalar_one_or_none()

    if not request:
        raise HTTPException(status_code=404, detail="Request not found")

    overseerr_service = OverseerrService()
    lifecycle_service = LifecycleService(db)

    if request.overseerr_request_id:
        success = await overseerr_service.approve_request(request.overseerr_request_id)
        if success:
            await lifecycle_service.transition(request_id, RequestStatus.SEARCHING)

    await overseerr_service.close()
    return RedirectResponse(url="/", status_code=303)


@router.post("/requests/{request_id}/deny")
async def deny_request(
    request_id: int,
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    """Decline a request in Overseerr and mark as failed."""
    result = await db.execute(select(RequestModel).where(RequestModel.id == request_id))
    request = result.scalar_one_or_none()

    if not request:
        raise HTTPException(status_code=404, detail="Request not found")

    overseerr_service = OverseerrService()
    lifecycle_service = LifecycleService(db)

    if request.overseerr_request_id:
        await overseerr_service.decline_request(request.overseerr_request_id)

    await lifecycle_service.mark_as_failed(request_id)
    await overseerr_service.close()
    return RedirectResponse(url="/", status_code=303)
