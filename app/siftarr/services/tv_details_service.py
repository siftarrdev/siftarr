"""TV season/episode data helpers and sync metadata computation."""

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import BackgroundTasks
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.siftarr.models.request import RequestStatus
from app.siftarr.services.background_tasks import (
    DETAILS_SYNC_TASKS,
    schedule_background_episode_refresh,
)
from app.siftarr.services.type_utils import normalize_int


def has_unresolved_partial_tv_data(
    seasons: list[Any],
    episodes_by_season: dict[int, list[Any]],
) -> bool:
    """Return True when season rows imply Plex enrichment still needs to run."""
    for season in seasons:
        season_episodes = episodes_by_season.get(season.id, [])
        if not season_episodes:
            return True

        episode_statuses = {
            getattr(episode.status, "value", episode.status) for episode in season_episodes
        }
        if RequestStatus.PENDING.value in episode_statuses:
            return True
    return False


def count_season_episode_states(episodes: list[Any]) -> dict[str, int]:
    """Count TV episode states for UI summaries."""
    counts = {"available": 0, "pending": 0, "unreleased": 0}
    for episode in episodes:
        status = getattr(episode.status, "value", episode.status)
        if status in counts:
            counts[status] += 1
    return counts


def count_request_episode_states(seasons_data: list[dict[str, object]]) -> dict[str, int]:
    """Aggregate TV episode counts across all serialized seasons."""
    return {
        "available": sum(normalize_int(season.get("available_count")) for season in seasons_data),
        "pending": sum(normalize_int(season.get("pending_count")) for season in seasons_data),
        "unreleased": sum(normalize_int(season.get("unreleased_count")) for season in seasons_data),
        "total": sum(normalize_int(season.get("total_count")) for season in seasons_data),
    }


def compute_sync_metadata(
    seasons: list[Any],
    episodes_by_season: dict[int, list[Any]],
    request_id: int,
    background_tasks: BackgroundTasks | None = None,
) -> dict[str, object]:
    """Build lightweight sync-state metadata for the TV details UI."""
    newest_synced = max((season.synced_at for season in seasons if season.synced_at), default=None)
    stale = False
    if newest_synced is None:
        stale = True
    else:
        newest = (
            newest_synced.replace(tzinfo=UTC) if newest_synced.tzinfo is None else newest_synced
        )
        stale = newest < (datetime.now(UTC) - timedelta(hours=24))

    missing = not seasons
    needs_plex_enrichment = has_unresolved_partial_tv_data(seasons, episodes_by_season)
    refresh_in_progress = request_id in DETAILS_SYNC_TASKS
    if (missing or stale or needs_plex_enrichment) and not refresh_in_progress:
        refresh_in_progress = schedule_background_episode_refresh(background_tasks, request_id)

    return {
        "has_cached_data": bool(seasons),
        "stale": stale,
        "needs_plex_enrichment": needs_plex_enrichment,
        "refresh_in_progress": refresh_in_progress,
        "last_synced_at": newest_synced.isoformat() if newest_synced else None,
    }


async def load_tv_seasons_with_episodes(
    db: AsyncSession,
    request_id: int,
) -> tuple[list[Any], list[Any]]:
    """Load seasons and episodes without per-season queries."""
    from app.siftarr.models.episode import Episode
    from app.siftarr.models.season import Season

    seasons_result = await db.execute(
        select(Season).where(Season.request_id == request_id).order_by(Season.season_number)
    )
    seasons = list(seasons_result.scalars().all())
    if not seasons:
        return [], []

    season_ids = [season.id for season in seasons]
    episodes_result = await db.execute(
        select(Episode)
        .where(Episode.season_id.in_(season_ids))
        .order_by(Episode.season_id, Episode.episode_number)
    )
    episodes = list(episodes_result.scalars().all())
    return seasons, episodes
