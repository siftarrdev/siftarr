"""Overseerr webhook handler for receiving media requests."""

import logging

from fastapi import APIRouter, BackgroundTasks, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.siftarr.database import async_session_maker, get_db
from app.siftarr.models import MediaType, Request, RequestStatus
from app.siftarr.services.episode_sync_service import EpisodeSyncService
from app.siftarr.services.media_helpers import extract_media_title_and_year
from app.siftarr.services.movie_decision_service import MovieDecisionService
from app.siftarr.services.overseerr_service import OverseerrService
from app.siftarr.services.plex_service import PlexService
from app.siftarr.services.prowlarr_service import ProwlarrService
from app.siftarr.services.qbittorrent_service import QbittorrentService
from app.siftarr.services.runtime_settings import get_effective_settings
from app.siftarr.services.tv_decision_service import TVDecisionService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhook", tags=["webhooks"])


class OverseerrMedia(BaseModel):
    """Media information from Overseerr webhook."""

    media_type: str = Field(description="Type: 'movie' or 'tv'")
    tmdbid: int | None = Field(default=None, description="TMDB ID")
    tvdbid: int | None = Field(default=None, description="TVDB ID")
    requested_seasons: list[int] | None = Field(default=None, description="Season numbers")
    requested_episodes: list[int] | None = Field(default=None, description="Episode numbers")


class OverseerrUser(BaseModel):
    """User information from Overseerr webhook."""

    username: str | None = None
    email: str | None = None


class OverseerrRequest(BaseModel):
    """Request information from Overseerr webhook."""

    id: int = Field(description="Overseerr request ID")


class OverseerrWebhookPayload(BaseModel):
    """Full webhook payload from Overseerr."""

    event: str = Field(description="Event type: 'mediarequested', 'mediaapproved', etc.")
    media: OverseerrMedia
    requestedBy: OverseerrUser | None = None
    request: OverseerrRequest | None = None


@router.post("/overseerr")
async def receive_overseerr_webhook(
    payload: OverseerrWebhookPayload,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> dict[str, object]:
    """Receive webhook from Overseerr and process the request.

    Args:
        payload: The webhook payload from Overseerr.
        background_tasks: FastAPI background tasks for async processing.
        db: Database session for request persistence.

    Returns:
        A dict containing status and request_id on success.
    """
    # Validate event type
    if payload.event not in ["mediarequested", "mediaapproved"]:
        return {"status": "ignored", "message": f"Event type '{payload.event}' not handled"}

    # Determine media type
    media_type = MediaType.MOVIE if payload.media.media_type == "movie" else MediaType.TV

    base_external_id = str(payload.media.tmdbid or payload.media.tvdbid)
    if payload.request and payload.request.id:
        external_id = f"{base_external_id}-{payload.request.id}"
    else:
        external_id = base_external_id

    # Deduplication: if both mediarequested and mediaapproved fire for the same
    # Overseerr request, skip creating a duplicate.
    if payload.request and payload.request.id:
        existing = await db.execute(
            select(Request).where(
                Request.overseerr_request_id == payload.request.id,
            )
        )
        if existing.scalar_one_or_none() is not None:
            return {
                "status": "duplicate",
                "message": f"Request for overseerr_request_id={payload.request.id} already exists",
            }
    else:
        existing = await db.execute(select(Request).where(Request.external_id == external_id))
        if existing.scalar_one_or_none() is not None:
            return {
                "status": "duplicate",
                "message": f"Request for external_id={external_id} already exists",
            }

    # Fetch title and year from Overseerr media details
    title = ""
    year = None
    media_external_id = payload.media.tmdbid or payload.media.tvdbid
    if media_external_id:
        settings = await get_effective_settings(db)
        overseerr_service = OverseerrService(settings=settings)
        try:
            media_type_for_api = "movie" if media_type == MediaType.MOVIE else "tv"
            title, year = await extract_media_title_and_year(
                overseerr_service, media_type_for_api, media_external_id
            )
        finally:
            await overseerr_service.close()

    # Create request record
    request = Request(
        external_id=external_id,
        media_type=media_type,
        tmdb_id=payload.media.tmdbid,
        tvdb_id=payload.media.tvdbid,
        title=title,
        year=year,
        requested_seasons=str(payload.media.requested_seasons)
        if payload.media.requested_seasons
        else None,
        requested_episodes=str(payload.media.requested_episodes)
        if payload.media.requested_episodes
        else None,
        requester_username=payload.requestedBy.username if payload.requestedBy else None,
        requester_email=payload.requestedBy.email if payload.requestedBy else None,
        status=RequestStatus.PENDING,
        overseerr_request_id=payload.request.id if payload.request else None,
    )
    db.add(request)
    await db.commit()
    await db.refresh(request)

    # Queue background task to process request (placeholder for now)
    background_tasks.add_task(process_request_background, request.id)

    return {"status": "accepted", "request_id": request.id}


async def process_request_background(request_id: int) -> None:
    """Background task to process a request.

    Args:
        request_id: The ID of the request to process.
    """
    async with async_session_maker() as db:
        try:
            result = await db.execute(select(Request).where(Request.id == request_id))
            request = result.scalar_one_or_none()

            if not request:
                logger.error("process_request_background: request %s not found", request_id)
                return

            logger.info(
                "Processing request: request_id=%s media_type=%s title=%s",
                request_id,
                request.media_type,
                request.title,
            )

            if request.media_type == MediaType.TV:
                settings = await get_effective_settings(db)
                plex_service = PlexService(settings=settings)
                episode_sync = EpisodeSyncService(db, plex=plex_service)
                try:
                    await episode_sync.sync_episodes(request.id)
                except Exception:
                    logger.exception("Episode sync failed for request_id=%s", request_id)
                finally:
                    await plex_service.close()

            settings = await get_effective_settings(db)
            prowlarr = ProwlarrService(settings=settings)
            qbittorrent = QbittorrentService(settings=settings)

            try:
                if request.media_type == MediaType.MOVIE:
                    decision_service = MovieDecisionService(db, prowlarr, qbittorrent)
                else:
                    decision_service = TVDecisionService(db, prowlarr, qbittorrent)

                result = await decision_service.process_request(request_id)
                logger.info(
                    "Request processing complete: request_id=%s status=%s",
                    request_id,
                    result.get("status"),
                )
            finally:
                pass
        except Exception:
            logger.exception("Error processing request: request_id=%s", request_id)
