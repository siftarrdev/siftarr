from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.siftarr.models import Episode, Season
from app.siftarr.models.request import MediaType, Request, RequestStatus
from tests.factories import make_request, make_season


async def make_tv_request(
    db: AsyncSession,
    *,
    seasons: dict[int, int] | None = None,
    episode_status: RequestStatus = RequestStatus.PENDING,
    **overrides,
) -> Request:
    """Create a persisted TV request with ``{season_number: episode_count}`` seasons."""
    overrides.setdefault("tmdb_id", 999)
    request = await make_request(db, media_type=MediaType.TV, **overrides)
    for number, count in (seasons or {1: 2}).items():
        await make_season(db, request, number, episodes=count, episode_status=episode_status)
    await db.commit()
    return request


async def episode_statuses(
    db: AsyncSession, request: Request
) -> dict[tuple[int, int], RequestStatus]:
    """Return ``{(season, episode): status}`` for a request from the database."""
    rows = await db.execute(
        select(Season.season_number, Episode.episode_number, Episode.status)
        .join(Episode, Episode.season_id == Season.id)
        .where(Season.request_id == request.id)
    )
    return {(season, episode): status for season, episode, status in rows}


async def request_status(db: AsyncSession, request: Request) -> RequestStatus:
    status = await db.scalar(select(Request.status).where(Request.id == request.id))
    assert status is not None
    return status


def recent_item(
    kind: str, tmdb_id: int, *, has_media: bool = True, minute: int = 0, rating_key: bool = True
) -> dict:
    """Build a Plex recently-added item for ``kind`` in ``{"movie", "show"}``."""
    item = {
        "type": kind,
        "rating_key": f"{kind}-{tmdb_id}",
        "title": f"{kind} {tmdb_id}",
        "added_at": int(datetime(2026, 4, 19, 12, minute, tzinfo=UTC).timestamp()),
        "guids": (f"tmdb://{tmdb_id}",),
        "Media": [{"id": 1}] if has_media else [],
    }
    if not rating_key:
        del item["rating_key"]
    return item


def set_recent_items(plex, *, movies=(), shows=(), movie_error: Exception | None = None) -> None:
    """Make ``plex.iter_recently_added_items`` yield the given items per media type."""

    async def iter_recently_added_items(media_type: str):
        if media_type == "movie":
            if movie_error is not None:
                raise movie_error
            for item in movies:
                yield item
        else:
            for item in shows:
                yield item

    plex.iter_recently_added_items = iter_recently_added_items
