"""Overseerr webhook handler for receiving media requests."""

from fastapi import APIRouter, BackgroundTasks, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.arbitratarr.database import get_db
from app.arbitratarr.models import MediaType, Request, RequestStatus

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

    # Use tmdbid or tvdbid as external_id
    external_id = str(payload.media.tmdbid or payload.media.tvdbid)

    # Create request record
    request = Request(
        external_id=external_id,
        media_type=media_type,
        tmdb_id=payload.media.tmdbid,
        tvdb_id=payload.media.tvdbid,
        title="",  # Will be populated from Overseerr API
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

    This is a placeholder that will be implemented in a later phase.

    Args:
        request_id: The ID of the request to process.
    """
    # TODO: Implement request processing logic in Phase 2.2+
    pass
