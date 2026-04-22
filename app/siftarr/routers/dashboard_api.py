"""Dashboard JSON API router for request details, search, and state mutations."""

import logging
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.siftarr.config import get_settings
from app.siftarr.database import get_db
from app.siftarr.models.request import MediaType
from app.siftarr.routers.dashboard_actions import _process_request_search
from app.siftarr.services.dashboard_service import (
    DashboardService,
    serialize_request_details_response,
    serialize_request_search_response,
    serialize_tv_search_response,
)
from app.siftarr.services.plex_service import PlexService
from app.siftarr.services.request_service import load_request_or_404, validate_tv_request
from app.siftarr.services.tv_details_service import (
    compute_sync_metadata,
    count_season_episode_states,
    load_tv_seasons_with_episodes,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/requests", tags=["dashboard-api"])


@router.get("/{request_id}/details")
async def request_details(
    request_id: int,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    request = await load_request_or_404(db, request_id)
    details = await DashboardService(db, settings=get_settings()).load_request_details(
        request,
        request_id=request_id,
        background_tasks=background_tasks,
    )
    return JSONResponse(serialize_request_details_response(details), background=background_tasks)


@router.post("/{request_id}/search")
async def search_request_releases(
    request_id: int,
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Trigger a search for a movie request and return updated releases as JSON."""
    request = await load_request_or_404(db, request_id)

    if request.media_type != MediaType.MOVIE:
        return JSONResponse(
            {"error": "Search endpoint is only available for movie requests"},
            status_code=400,
        )

    await _process_request_search(request, db)
    search_data = await DashboardService(db, settings=get_settings()).load_movie_search_results(
        request,
        request_id=request_id,
    )
    return JSONResponse(serialize_request_search_response(search_data))


@router.get("/{request_id}/seasons")
async def get_request_seasons(
    request_id: int,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Get seasons and episodes for a TV request."""
    request = await load_request_or_404(db, request_id)

    if request.media_type != MediaType.TV:
        return JSONResponse({"seasons": [], "message": "Request is not a TV show"})

    seasons, episodes = await load_tv_seasons_with_episodes(db, request_id)
    episodes_by_season: dict[int, list[Any]] = {}
    for episode in episodes:
        episodes_by_season.setdefault(episode.season_id, []).append(episode)
    sync_state = compute_sync_metadata(
        seasons,
        episodes_by_season,
        request_id,
        background_tasks,
    )

    seasons_data = []
    for season in seasons:
        season_episodes = episodes_by_season.get(season.id, [])

        season_data = {
            "id": season.id,
            "season_number": season.season_number,
            "status": season.status.value,
            "synced_at": season.synced_at.isoformat() if season.synced_at else None,
            "pending_count": count_season_episode_states(season_episodes)["pending"],
            "unreleased_count": count_season_episode_states(season_episodes)["unreleased"],
            "episodes": [
                {
                    "id": ep.id,
                    "episode_number": ep.episode_number,
                    "title": ep.title,
                    "air_date": ep.air_date.isoformat() if ep.air_date else None,
                    "status": ep.status.value,
                }
                for ep in season_episodes
            ],
        }
        seasons_data.append(season_data)

    return JSONResponse(
        {"seasons": seasons_data, "sync_state": sync_state},
        background=background_tasks,
    )


@router.post("/{request_id}/seasons/{season_number}/search")
async def search_season_packs(
    request_id: int,
    season_number: int,
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Search for season packs for a specific season."""
    request = await load_request_or_404(db, request_id)
    validate_tv_request(request)
    search_data = await DashboardService(db, settings=get_settings()).search_season_packs(
        request,
        season_number=season_number,
    )
    return JSONResponse(serialize_tv_search_response(search_data))


@router.post("/{request_id}/seasons/search-all")
async def search_all_season_packs(
    request_id: int,
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Search broadly for TV season packs without downloading anything."""
    request = await load_request_or_404(db, request_id)
    validate_tv_request(request)
    search_data = await DashboardService(db, settings=get_settings()).search_all_season_packs(
        request,
        request_id=request_id,
    )
    return JSONResponse(serialize_tv_search_response(search_data))


@router.post("/{request_id}/seasons/{season_number}/episodes/{episode_number}/search")
async def search_episode(
    request_id: int,
    season_number: int,
    episode_number: int,
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Search for a specific episode."""
    request = await load_request_or_404(db, request_id)
    validate_tv_request(request)
    search_data = await DashboardService(db, settings=get_settings()).search_episode(
        request,
        season_number=season_number,
        episode_number=episode_number,
    )
    return JSONResponse(serialize_tv_search_response(search_data))


@router.post("/{request_id}/refresh-plex")
async def refresh_plex(
    request_id: int,
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Run the simplified TV sync flow for one request."""
    from app.siftarr.services.episode_sync_service import EpisodeSyncService

    request = await load_request_or_404(db, request_id)

    if request.media_type != MediaType.TV:
        return JSONResponse({"error": "Request is not a TV show"})

    effective_settings = get_settings()
    plex_service = PlexService(settings=effective_settings)

    try:
        episode_sync = EpisodeSyncService(db, plex=plex_service)
        await episode_sync.sync_request(request_id)
        return JSONResponse({"status": "success", "message": "Plex sync completed"})
    except Exception:
        logger.exception("Plex refresh failed for request_id=%s", request_id)
        return JSONResponse({"status": "error", "message": "Plex sync failed"}, status_code=500)
    finally:
        await plex_service.close()
