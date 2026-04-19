"""Staged torrent management router."""

import os
import re
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Form, HTTPException
from fastapi import Request as FastAPIRequest
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.siftarr.database import get_db
from app.siftarr.models.request import (
    MediaType,
    Request,
    RequestStatus,
    is_active_staging_workflow_status,
)
from app.siftarr.models.staged_torrent import StagedTorrent
from app.siftarr.services.lifecycle_service import LifecycleService
from app.siftarr.services.qbittorrent_service import MediaCategory, QbittorrentService
from app.siftarr.services.runtime_settings import get_effective_settings
from app.siftarr.services.staging_decision_logger import (
    log_replacement_decision,
    log_staging_decision,
)

_BTIH_RE = re.compile(r"urn:btih:([0-9a-fA-F]{40}|[2-7A-Za-z]{32})", re.IGNORECASE)

router = APIRouter(prefix="/staged", tags=["staged"])


def _wants_json(http_request: FastAPIRequest) -> bool:
    return "application/json" in http_request.headers.get("accept", "")


async def _finalize_action_response(
    http_request: FastAPIRequest,
    message: str,
    *,
    redirect_url: str = "/?tab=staged",
):
    if _wants_json(http_request):
        return JSONResponse({"status": "ok", "message": message})
    return RedirectResponse(url=redirect_url, status_code=303)


async def _approve_torrent(torrent: StagedTorrent, db: AsyncSession) -> bool:
    request = None
    if torrent.request_id:
        result = await db.execute(select(Request).where(Request.id == torrent.request_id))
        request = result.scalar_one_or_none()

    category = (
        MediaCategory.MOVIES
        if request and request.media_type == MediaType.MOVIE
        else MediaCategory.TV
    )

    rules_selected_torrent = None
    if request is not None:
        rules_selected_result = await db.execute(
            select(StagedTorrent)
            .where(
                StagedTorrent.request_id == request.id,
                StagedTorrent.selection_source == "rule",
                StagedTorrent.status.in_(["staged", "approved"]),
            )
            .order_by(StagedTorrent.score.desc(), StagedTorrent.created_at.asc())
        )
        rules_selected_torrent = rules_selected_result.scalars().first()

    runtime_settings = await get_effective_settings(db)
    qbittorrent = QbittorrentService(settings=runtime_settings)

    if torrent.magnet_url:
        torrent_hash = await qbittorrent.add_torrent(
            magnet_uri=torrent.magnet_url, category=category
        )
        success = torrent_hash is not None
    else:
        success = (
            await qbittorrent.add_torrent(torrent_path=torrent.torrent_path, category=category)
            is not None
        )

    if not success:
        return False

    log_staging_decision(
        request=request,
        approved_torrent=torrent,
        rules_selected_torrent=rules_selected_torrent,
    )
    torrent.status = "approved"
    if request:
        lifecycle_service = LifecycleService(db)
        if request.status not in (
            RequestStatus.COMPLETED,
            RequestStatus.FAILED,
            RequestStatus.DENIED,
        ):
            await lifecycle_service.mark_as_downloading(request.id)

    try:
        if os.path.exists(torrent.torrent_path):
            os.remove(torrent.torrent_path)
        if os.path.exists(torrent.json_path):
            os.remove(torrent.json_path)
    except OSError:
        pass

    return True


async def _discard_torrent(torrent: StagedTorrent, db: AsyncSession) -> bool:
    if torrent.request_id:
        result = await db.execute(select(Request).where(Request.id == torrent.request_id))
        request = result.scalar_one_or_none()
        if request:
            if request.status == RequestStatus.DOWNLOADING:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "Cannot discard a torrent that is already downloading. Use Replace instead to select a different torrent."
                    ),
                )
            if request.status == RequestStatus.STAGED:
                lifecycle_service = LifecycleService(db)
                await lifecycle_service.mark_as_pending(torrent.request_id)

    torrent.status = "discarded"

    try:
        if os.path.exists(torrent.torrent_path):
            os.remove(torrent.torrent_path)
        if os.path.exists(torrent.json_path):
            os.remove(torrent.json_path)
    except OSError:
        pass

    return True


@router.post("/{torrent_id}/approve", response_model=None)
async def approve_staged_torrent(
    torrent_id: int,
    http_request: FastAPIRequest,
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse | JSONResponse:
    """Approve a staged torrent - send to qBittorrent."""
    result = await db.execute(select(StagedTorrent).where(StagedTorrent.id == torrent_id))
    torrent = result.scalar_one_or_none()

    if not torrent:
        raise HTTPException(status_code=404, detail="Staged torrent not found")

    success = await _approve_torrent(torrent, db)
    await db.commit()

    if not success:
        raise HTTPException(status_code=500, detail="Failed to approve staged torrent")
    return await _finalize_action_response(
        http_request,
        "Torrent approved successfully",
        redirect_url="/?tab=staged",
    )


@router.post("/{torrent_id}/discard", response_model=None)
async def discard_staged_torrent(
    torrent_id: int,
    http_request: FastAPIRequest,
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse | JSONResponse:
    """Discard a staged torrent - delete files."""
    result = await db.execute(select(StagedTorrent).where(StagedTorrent.id == torrent_id))
    torrent = result.scalar_one_or_none()

    if not torrent:
        raise HTTPException(status_code=404, detail="Staged torrent not found")

    await _discard_torrent(torrent, db)
    await db.commit()

    return await _finalize_action_response(
        http_request,
        "Torrent discarded successfully",
        redirect_url="/?tab=staged",
    )


@router.post("/bulk", response_model=None)
async def bulk_staged_action(
    http_request: FastAPIRequest,
    action: str = Form(...),
    torrent_ids: list[int] = Form(default=[]),
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse | JSONResponse:
    """Apply an approve/discard action to multiple staged torrents."""
    if not torrent_ids:
        return await _finalize_action_response(
            http_request,
            "No staged torrents were selected.",
            redirect_url="/?tab=staged",
        )

    result = await db.execute(select(StagedTorrent).where(StagedTorrent.id.in_(torrent_ids)))
    torrents = list(result.scalars().all())

    if action not in {"approve", "discard"}:
        raise HTTPException(status_code=400, detail="Invalid bulk action")

    processed = 0
    for torrent in torrents:
        if action == "approve":
            success = await _approve_torrent(torrent, db)
        else:
            success = await _discard_torrent(torrent, db)
        if success:
            processed += 1

    await db.commit()
    action_label = "Approved" if action == "approve" else "Discarded"
    message = f"{action_label} {processed} staged torrent(s)."
    return await _finalize_action_response(
        http_request,
        message,
        redirect_url="/?tab=staged",
    )


@router.post("/{torrent_id}/replace")
async def replace_staged_torrent(
    torrent_id: int,
    reason: str | None = Form(None),
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    """Replace an approved torrent with a new staged one."""
    # Get the new torrent (the one being approved)
    result = await db.execute(select(StagedTorrent).where(StagedTorrent.id == torrent_id))
    new_torrent = result.scalar_one_or_none()

    if not new_torrent:
        raise HTTPException(status_code=404, detail="Staged torrent not found")

    # Handle case where torrent has no request_id (manual add)
    if not new_torrent.request_id:
        raise HTTPException(
            status_code=400,
            detail="Cannot replace torrent without an associated request",
        )

    # Find the request associated with this torrent
    result = await db.execute(select(Request).where(Request.id == new_torrent.request_id))
    request = result.scalar_one_or_none()

    if not request:
        raise HTTPException(status_code=404, detail="Associated request not found")

    # Find the currently approved torrent for this request (the one being replaced)
    result = await db.execute(
        select(StagedTorrent).where(
            StagedTorrent.request_id == request.id,
            StagedTorrent.status == "approved",
        )
    )
    old_torrent = result.scalar_one_or_none()

    if not old_torrent:
        raise HTTPException(
            status_code=400,
            detail="No approved torrent found to replace for this request",
        )

    # Determine category
    category = MediaCategory.TV
    if request.media_type == MediaType.MOVIE:
        category = MediaCategory.MOVIES

    # Add new torrent to qBittorrent
    runtime_settings = await get_effective_settings(db)
    qbittorrent = QbittorrentService(settings=runtime_settings)
    success = False

    if new_torrent.magnet_url:
        torrent_hash = await qbittorrent.add_torrent(
            magnet_uri=new_torrent.magnet_url,
            category=category,
        )
        success = torrent_hash is not None
    else:
        success = (
            await qbittorrent.add_torrent(
                torrent_path=new_torrent.torrent_path,
                category=category,
            )
            is not None
        )

    if success:
        # Log the replacement decision
        log_replacement_decision(
            request=request,
            new_torrent=new_torrent,
            replaced_torrent=old_torrent,
            reason=reason,
        )

        # Mark the old torrent as replaced
        old_torrent.status = "replaced"
        old_torrent.replaced_by_id = new_torrent.id
        old_torrent.replaced_at = datetime.now(UTC)
        old_torrent.replacement_reason = reason

        # Mark the new torrent as approved
        new_torrent.status = "approved"

        # Delete staging files for the new torrent
        try:
            if os.path.exists(new_torrent.torrent_path):
                os.remove(new_torrent.torrent_path)
            if os.path.exists(new_torrent.json_path):
                os.remove(new_torrent.json_path)
        except OSError:
            pass

    await db.commit()

    return RedirectResponse(url="/?tab=staged", status_code=303)


@router.get("/download-status")
async def get_download_status(
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Return qBittorrent progress for all approved torrents."""
    result = await db.execute(select(StagedTorrent).where(StagedTorrent.status == "approved"))
    torrents = list(result.scalars().all())

    if not torrents:
        return JSONResponse({"torrents": []})

    # Collect request IDs for status lookup
    request_ids = {t.request_id for t in torrents if t.request_id is not None}
    request_statuses: dict[int, RequestStatus] = {}
    if request_ids:
        req_result = await db.execute(
            select(Request.id, Request.status).where(Request.id.in_(request_ids))
        )
        for req_id, req_status in req_result.all():
            request_statuses[req_id] = req_status

    torrents = [
        torrent
        for torrent in torrents
        if torrent.request_id is None
        or is_active_staging_workflow_status(request_statuses.get(torrent.request_id))
    ]

    if not torrents:
        return JSONResponse({"torrents": []})

    runtime_settings = await get_effective_settings(db)
    qbittorrent = QbittorrentService(settings=runtime_settings)

    torrent_data = []
    for torrent in torrents:
        qbit_progress: float | None = None
        qbit_state: str | None = None

        # Try to get progress via hash first, then fall back to name
        torrent_hash: str | None = None
        if torrent.magnet_url:
            m = _BTIH_RE.search(torrent.magnet_url)
            if m:
                torrent_hash = m.group(1).lower()

        if torrent_hash:
            info = await qbittorrent.get_torrent_info(torrent_hash)
            if info:
                qbit_progress = info["progress"]
                qbit_state = info["state"]
        else:
            qbit_progress = await qbittorrent.get_torrent_progress_by_name(torrent.title)

        request_status_value = request_statuses.get(torrent.request_id or -1)
        if isinstance(request_status_value, RequestStatus):
            request_status = request_status_value.value
        elif request_status_value is not None:
            request_status = str(request_status_value)
        else:
            request_status = "unknown"

        torrent_data.append(
            {
                "id": torrent.id,
                "title": torrent.title,
                "request_id": torrent.request_id,
                "request_status": request_status,
                "qbit_progress": qbit_progress,
                "qbit_state": qbit_state,
            }
        )

    return JSONResponse({"torrents": torrent_data})
