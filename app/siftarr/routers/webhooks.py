"""Overseerr webhook handler for receiving media requests."""

import contextlib
import logging
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.siftarr import database as db_mod
from app.siftarr.config import get_settings
from app.siftarr.database import get_db, init_engine
from app.siftarr.models import MediaType, Request, RequestStatus
from app.siftarr.services.lifecycle.episode_sync_service import EpisodeSyncService
from app.siftarr.services.utils.media_helpers import extract_media_title_and_year
from app.siftarr.services.decisions.movie_decision_service import MovieDecisionService
from app.siftarr.services.integrations.overseerr_service import OverseerrService
from app.siftarr.services.integrations.plex_service import PlexService
from app.siftarr.services.integrations.prowlarr_service import ProwlarrService
from app.siftarr.services.integrations.qbittorrent_service import QbittorrentService
from app.siftarr.services.decisions.tv_decision_service import TVDecisionService
from app.siftarr.services.lifecycle.unreleased_service import UnreleasedEvaluator

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
    created_at: str | None = None


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
        settings = get_settings()
        overseerr_service = OverseerrService(settings=settings)
        media_type_for_api = "movie" if media_type == MediaType.MOVIE else "tv"
        title, year = await extract_media_title_and_year(
            overseerr_service, media_type_for_api, media_external_id
        )

    # Parse created_at from Overseerr webhook
    created_at = None
    if payload.request and payload.request.created_at:
        with contextlib.suppress(ValueError, TypeError):
            created_at = datetime.fromisoformat(payload.request.created_at.replace("Z", "+00:00"))

    # Create request record
    request = Request(
        external_id=external_id,
        media_type=media_type,
        tmdb_id=payload.media.tmdbid,
        tvdb_id=payload.media.tvdbid,
        title=title,
        year=year,
        requester_username=payload.requestedBy.username if payload.requestedBy else None,
        requester_email=payload.requestedBy.email if payload.requestedBy else None,
        status=RequestStatus.PENDING,
        overseerr_request_id=payload.request.id if payload.request else None,
        created_at=created_at,
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
    if db_mod.async_session_maker is None:
        init_engine()
    assert db_mod.async_session_maker is not None
    async with db_mod.async_session_maker() as db:
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
                settings = get_settings()
                plex_service = PlexService(settings=settings)
                episode_sync = EpisodeSyncService(db, plex=plex_service)
                try:
                    await episode_sync.sync_request(request.id)
                except Exception:
                    logger.exception("Episode sync failed for request_id=%s", request_id)

            settings = get_settings()
            overseerr = OverseerrService(settings=settings)
            evaluator = UnreleasedEvaluator(db, overseerr)
            try:
                new_status = await evaluator.evaluate_and_apply(request)
            except Exception:
                logger.exception("Unreleased evaluation failed for request_id=%s", request_id)
                new_status = None
            if new_status == RequestStatus.UNRELEASED:
                logger.info("Request %s classified as unreleased; skipping search", request_id)
                return
            await db.refresh(request)

            settings = get_settings()
            prowlarr = ProwlarrService(settings=settings)
            qbittorrent = QbittorrentService(settings=settings)

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
        except Exception:
            logger.exception("Error processing request: request_id=%s", request_id)
