"""Authenticated download health and operator-confirmed recovery API."""

from typing import Literal

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.siftarr.database import get_db
from app.siftarr.services.auth_service import require_session_or_api_key
from app.siftarr.services.integrations.qbittorrent_service import QbittorrentService
from app.siftarr.services.lifecycle.download_health_service import (
    DownloadHealthNotFound,
    DownloadHealthService,
    DownloadRecoveryNotEligible,
)

router = APIRouter(
    prefix="/api/download-health",
    tags=["download-health"],
    dependencies=[Depends(require_session_or_api_key)],
)


class RecoveryRequest(BaseModel):
    """Explicit confirmation and disposition for a replacement review."""

    confirm: bool
    disposition: Literal["cooldown", "permanent_rejection"] = "cooldown"


@router.get("")
async def list_download_health(db: AsyncSession = Depends(get_db)) -> dict[str, object]:
    """List persisted warnings, missing torrents, and recovery offers."""
    problems = await DownloadHealthService(db).list_problems()
    return {"problems": problems, "count": len(problems)}


@router.post("/{attempt_id}/recover")
async def recover_download_health(
    attempt_id: int,
    payload: RecoveryRequest = Body(...),
    db: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    """Confirm recovery without deleting the original qBittorrent torrent/files."""
    try:
        service = DownloadHealthService(db, QbittorrentService())
        return await service.recover(
            attempt_id,
            confirm=payload.confirm,
            disposition=payload.disposition,
        )
    except DownloadHealthNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except DownloadRecoveryNotEligible as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
