"""Keep one local request per movie or show and link every Seerr request to it.

Seerr creates a separate request for each season request and for each re-request
after a deletion. Siftarr tracks a whole movie or show per row, so a new Seerr
request for media that already has a row links to that row instead of creating
a duplicate.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.siftarr.models.activity_log import EventType
from app.siftarr.models.episode import Episode
from app.siftarr.models.request import MediaType, Request, RequestStatus
from app.siftarr.models.request_overseerr_link import RequestOverseerrLink
from app.siftarr.models.season import Season
from app.siftarr.services.lifecycle.activity_log_service import ActivityLogService

# A new Seerr request for media in one of these states means the user wants it
# again, so the row goes back to PENDING. Episode sync and Plex checks then
# settle the real state.
REACTIVATE_ON_LINK_STATUSES: frozenset[RequestStatus] = frozenset(
    {RequestStatus.DENIED, RequestStatus.FAILED, RequestStatus.COMPLETED}
)
_RESET_EPISODE_STATUSES = (RequestStatus.DENIED, RequestStatus.FAILED)


async def linked_overseerr_request_ids(db: AsyncSession) -> set[int]:
    """Return every Seerr request ID that already has a local request."""
    linked = await db.execute(select(RequestOverseerrLink.overseerr_request_id))
    primary = await db.execute(
        select(Request.overseerr_request_id).where(Request.overseerr_request_id.is_not(None))
    )
    return {row[0] for row in linked.all()} | {row[0] for row in primary.all()}


async def get_linked_overseerr_request_ids(db: AsyncSession, request: Request) -> list[int]:
    """Return all Seerr request IDs linked to one local request, primary first."""
    result = await db.execute(
        select(RequestOverseerrLink.overseerr_request_id)
        .where(RequestOverseerrLink.request_id == request.id)
        .order_by(RequestOverseerrLink.id)
    )
    ids = [row[0] for row in result.all()]
    primary = request.overseerr_request_id
    if isinstance(primary, int) and primary > 0:
        ids = [primary, *(i for i in ids if i != primary)]
    return ids


async def find_request_for_media(
    db: AsyncSession,
    media_type: MediaType,
    tmdb_id: int | None,
    tvdb_id: int | None,
) -> Request | None:
    """Find the local request for a movie or show, preferring an active row."""
    if tmdb_id is not None:
        match = Request.tmdb_id == tmdb_id
    elif tvdb_id is not None:
        match = Request.tvdb_id == tvdb_id
    else:
        return None
    result = await db.execute(
        select(Request).where(Request.media_type == media_type, match).order_by(Request.id.desc())
    )
    candidates = list(result.scalars().all())
    for candidate in candidates:
        if candidate.status != RequestStatus.DENIED:
            return candidate
    return candidates[0] if candidates else None


async def add_overseerr_link(db: AsyncSession, request: Request, overseerr_request_id: int) -> None:
    """Record a Seerr request as linked to a local request."""
    db.add(RequestOverseerrLink(request_id=request.id, overseerr_request_id=overseerr_request_id))
    if request.overseerr_request_id is None:
        request.overseerr_request_id = overseerr_request_id
    await db.flush()


async def attach_overseerr_request(
    db: AsyncSession, request: Request, overseerr_request_id: int
) -> bool:
    """Link a new Seerr request to an existing local request.

    Returns True if the request was reactivated and needs processing again.
    """
    await add_overseerr_link(db, request, overseerr_request_id)
    old_status = request.status
    reactivated = old_status in REACTIVATE_ON_LINK_STATUSES
    if reactivated:
        if request.media_type == MediaType.TV:
            episodes = await db.execute(
                select(Episode)
                .join(Season, Episode.season_id == Season.id)
                .where(Season.request_id == request.id)
                .where(Episode.status.in_(_RESET_EPISODE_STATUSES))
            )
            for episode in episodes.scalars().all():
                episode.status = RequestStatus.PENDING
        request.status = RequestStatus.PENDING
        request.rejection_reason = None
        request.retry_count = 0
        request.next_retry_at = None
        await db.flush()

    await ActivityLogService(db).log(
        EventType.REQUEST_STATUS_CHANGED,
        request_id=request.id,
        details={
            "action": "overseerr_request_linked",
            "overseerr_request_id": overseerr_request_id,
            "old_status": old_status.value,
            "new_status": request.status.value,
        },
    )
    return reactivated
