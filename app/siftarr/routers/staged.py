"""Staged torrent management router."""

import os
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Form, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.siftarr.database import get_db
from app.siftarr.models.request import MediaType, Request
from app.siftarr.models.staged_torrent import StagedTorrent
from app.siftarr.services.lifecycle_service import LifecycleService
from app.siftarr.services.qbittorrent_service import MediaCategory, QbittorrentService
from app.siftarr.services.runtime_settings import get_effective_settings
from app.siftarr.services.staging_decision_logger import log_staging_decision

router = APIRouter(prefix="/staged", tags=["staged"])


@router.post("/{torrent_id}/approve")
async def approve_staged_torrent(
    torrent_id: int,
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    """Approve a staged torrent - send to qBittorrent."""
    result = await db.execute(select(StagedTorrent).where(StagedTorrent.id == torrent_id))
    torrent = result.scalar_one_or_none()

    if not torrent:
        raise HTTPException(status_code=404, detail="Staged torrent not found")

    # Get request to determine category
    request = None
    if torrent.request_id:
        result = await db.execute(select(Request).where(Request.id == torrent.request_id))
        request = result.scalar_one_or_none()

    # Determine category
    category = MediaCategory.TV
    if request and request.media_type == MediaType.MOVIE:
        category = MediaCategory.MOVIES

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

    # Add to qBittorrent
    runtime_settings = await get_effective_settings(db)
    qbittorrent = QbittorrentService(settings=runtime_settings)
    success = False

    if torrent.magnet_url:
        torrent_hash = await qbittorrent.add_torrent(
            magnet_uri=torrent.magnet_url,
            category=category,
        )
        success = torrent_hash is not None
    else:
        success = (
            await qbittorrent.add_torrent(
                torrent_path=torrent.torrent_path,
                category=category,
            )
            is not None
        )

    if success:
        log_staging_decision(
            request=request,
            approved_torrent=torrent,
            rules_selected_torrent=rules_selected_torrent,
        )

        # Update torrent status
        torrent.status = "approved"

        # Update request status if exists
        if request:
            lifecycle_service = LifecycleService(db)
            await lifecycle_service.mark_as_downloading(request.id)

        # Delete staging files
        try:
            if os.path.exists(torrent.torrent_path):
                os.remove(torrent.torrent_path)
            if os.path.exists(torrent.json_path):
                os.remove(torrent.json_path)
        except OSError:
            pass

    await db.commit()

    return RedirectResponse(url="/", status_code=303)


@router.post("/{torrent_id}/discard")
async def discard_staged_torrent(
    torrent_id: int,
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    """Discard a staged torrent - delete files."""
    from app.siftarr.models.request import RequestStatus

    result = await db.execute(select(StagedTorrent).where(StagedTorrent.id == torrent_id))
    torrent = result.scalar_one_or_none()

    if not torrent:
        raise HTTPException(status_code=404, detail="Staged torrent not found")

    # Check request status before allowing discard
    if torrent.request_id:
        result = await db.execute(select(Request).where(Request.id == torrent.request_id))
        request = result.scalar_one_or_none()
        if request:
            if request.status == RequestStatus.DOWNLOADING:
                raise HTTPException(
                    status_code=400,
                    detail="Cannot discard a torrent that is already downloading. Use Replace instead to select a different torrent.",
                )
            # Only transition to pending if currently staged
            if request.status == RequestStatus.STAGED:
                lifecycle_service = LifecycleService(db)
                await lifecycle_service.mark_as_pending(torrent.request_id)

    # Update torrent status
    torrent.status = "discarded"

    # Delete staging files
    try:
        if os.path.exists(torrent.torrent_path):
            os.remove(torrent.torrent_path)
        if os.path.exists(torrent.json_path):
            os.remove(torrent.json_path)
    except OSError:
        pass

    await db.commit()

    return RedirectResponse(url="/", status_code=303)


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

    # Find the rules-selected torrent for logging
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
        log_staging_decision(
            request=request,
            approved_torrent=new_torrent,
            rules_selected_torrent=rules_selected_torrent,
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

    return RedirectResponse(url="/", status_code=303)
