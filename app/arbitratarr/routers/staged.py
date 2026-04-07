"""Staged torrent management router."""

import os

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.arbitratarr.database import get_db
from app.arbitratarr.models.request import MediaType, Request
from app.arbitratarr.models.staged_torrent import StagedTorrent
from app.arbitratarr.services.lifecycle_service import LifecycleService
from app.arbitratarr.services.qbittorrent_service import MediaCategory, QbittorrentService
from app.arbitratarr.services.runtime_settings import get_effective_settings
from app.arbitratarr.services.staging_decision_logger import log_staging_decision

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
    result = await db.execute(select(StagedTorrent).where(StagedTorrent.id == torrent_id))
    torrent = result.scalar_one_or_none()

    if not torrent:
        raise HTTPException(status_code=404, detail="Staged torrent not found")

    # Update torrent status
    torrent.status = "discarded"

    # Update request status if exists
    if torrent.request_id:
        result = await db.execute(select(Request).where(Request.id == torrent.request_id))
        request = result.scalar_one_or_none()
        if request:
            lifecycle_service = LifecycleService(db)
            await lifecycle_service.mark_as_pending(torrent.request_id)

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
