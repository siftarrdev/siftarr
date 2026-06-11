"""Dashboard form-POST actions router for request lifecycle operations."""

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, Form, HTTPException
from fastapi import Request as FastAPIRequest
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.siftarr.config import get_settings
from app.siftarr.database import get_db
from app.siftarr.models import EventType
from app.siftarr.models.episode import Episode
from app.siftarr.models.release import Release
from app.siftarr.models.request import MediaType, RequestStatus
from app.siftarr.models.request import Request as RequestModel
from app.siftarr.models.season import Season
from app.siftarr.services.dashboard.search_service import SearchService
from app.siftarr.services.integrations.overseerr_service import OverseerrService
from app.siftarr.services.integrations.prowlarr_service import ProwlarrRelease
from app.siftarr.services.lifecycle.activity_log_service import ActivityLogService
from app.siftarr.services.lifecycle.episode_derive import (
    derive_request_status_from_episodes,
    derive_season_status,
)
from app.siftarr.services.lifecycle.lifecycle_service import LifecycleService
from app.siftarr.services.lifecycle.pending_queue_service import PendingQueueService
from app.siftarr.services.releases.staging_service import StagingService
from app.siftarr.services.request_service import (
    bulk_redirect_url,
    load_request_or_404,
    selection_redirect_url,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/requests", tags=["dashboard-actions"])


def _release_has_usable_url():
    return or_(
        func.length(func.trim(Release.magnet_url)) > 0,
        func.length(func.trim(Release.download_url)) > 0,
    )


async def _load_all_pending_search_requests(db: AsyncSession) -> list[RequestModel]:
    """Load requests targeted by Search All bulk search actions.

    For TV, includes requests where any episode has PENDING status
    (since request.status is episode-derived).
    """
    tv_pending_ids_subq = (
        select(Season.request_id)
        .select_from(Episode)
        .join(Season, Episode.season_id == Season.id)
        .where(Episode.status == RequestStatus.PENDING)
    ).subquery()
    result = await db.execute(
        select(RequestModel)
        .where(
            or_(
                RequestModel.status.in_([RequestStatus.PENDING, RequestStatus.SEARCHING]),
                RequestModel.id.in_(select(tv_pending_ids_subq.c.request_id)),
            )
        )
        .order_by(RequestModel.created_at.desc())
    )
    return list(result.scalars().all())


def _selection_success_message(result: dict[str, object]) -> str:
    """Return a clear response message for release-selection actions."""
    action = result.get("action")
    if action == "auto_staged":
        return "Request auto-staged successfully"
    if action == "replaced_active_selection":
        return "Active staged selection replaced successfully"
    if action == "manual_staged":
        return "Request manually staged successfully"
    return str(result.get("message") or "Torrent sent successfully")


def _json_action_response(message: str, redirect_to: str) -> JSONResponse:
    """Return the standard JSON shape used by dashboard fetch actions."""
    return JSONResponse({"status": "ok", "message": message, "redirect_to": redirect_to})


async def _deny_request_record(
    request: RequestModel,
    db: AsyncSession,
    reason: str | None = None,
) -> None:
    """Decline a request in Overseerr and mark it denied locally."""
    effective_settings = get_settings()
    overseerr_service = OverseerrService(settings=effective_settings)
    lifecycle_service = LifecycleService(db)
    queue_service = PendingQueueService(db)

    if request.overseerr_request_id:
        try:
            declined = await overseerr_service.decline_request(
                request.overseerr_request_id,
                reason=reason,
            )
            if not declined:
                logger.warning(
                    "Overseerr decline returned false for request_id=%s overseerr_request_id=%s; continuing local deny cleanup",
                    request.id,
                    request.overseerr_request_id,
                )
        except Exception:
            logger.warning(
                "Overseerr decline failed for request_id=%s overseerr_request_id=%s; continuing local deny cleanup",
                request.id,
                request.overseerr_request_id,
                exc_info=True,
            )

    await queue_service.remove_from_queue(request.id)
    await lifecycle_service.transition(request.id, RequestStatus.DENIED, reason=reason)


@router.post("/{request_id}/search")
async def search_request_now(
    request_id: int,
    redirect_to: str | None = Form(default=None),
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    """Trigger a manual torrent search for a request."""
    request = await load_request_or_404(db, request_id)

    service = SearchService(db)
    await service.process_request_search(request)
    return RedirectResponse(url=redirect_to or "/?tab=pending", status_code=303)


@router.post("/bulk", response_model=None)
async def bulk_request_action(
    http_request: FastAPIRequest,
    action: str = Form(...),
    request_ids: list[int] = Form(default=[]),
    redirect_to: str | None = Form(default=None),
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse | JSONResponse:
    """Apply a bulk action to selected requests."""
    wants_json = "application/json" in http_request.headers.get("accept", "")
    redirect_url = bulk_redirect_url(redirect_to)
    if action == "search_all_pending":
        requests = await _load_all_pending_search_requests(db)
        search_service = SearchService(db)
        for request in requests:
            await search_service.process_request_search(request)
        if wants_json:
            return _json_action_response("Search started", redirect_url)
        return RedirectResponse(url=redirect_url, status_code=303)

    if not request_ids:
        if wants_json:
            return _json_action_response("No items selected", redirect_url)
        return RedirectResponse(url=redirect_url, status_code=303)

    result = await db.execute(
        select(RequestModel)
        .where(RequestModel.id.in_(request_ids))
        .order_by(RequestModel.created_at.desc())
    )
    requests = list(result.scalars().all())

    count = 0
    search_service = SearchService(db)
    for request in requests:
        if action == "search":
            await search_service.process_request_search(request)
        elif action == "deny":
            await _deny_request_record(request, db, reason="Bulk denied")
            count += 1

    if wants_json:
        return _json_action_response(f"Denied {count} request(s)", redirect_url)
    return RedirectResponse(url=redirect_url, status_code=303)


@router.post("/{request_id}/releases/{release_id}/use", response_model=None)
async def use_request_release(
    request_id: int,
    release_id: int,
    http_request: FastAPIRequest,
    redirect_to: str | None = Form(default=None),
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse | JSONResponse:
    """Stage or send a selected stored release for a request."""
    request = await load_request_or_404(db, request_id)

    release_result = await db.execute(
        select(Release).where(Release.id == release_id, Release.request_id == request_id)
    )
    release = release_result.scalar_one_or_none()
    if not release:
        raise HTTPException(status_code=404, detail="Release not found")

    result = await StagingService(db).use_releases(request, [release], selection_source="manual")
    if "application/json" in http_request.headers.get("accept", ""):
        return JSONResponse(
            {
                "status": "ok",
                "message": _selection_success_message(result),
            }
        )
    return RedirectResponse(
        url=selection_redirect_url(
            redirect_to,
            request,
            prefer_staged_view=result.get("status") == "staged",
        ),
        status_code=303,
    )


@router.post("/{request_id}/seasons/{season_number}/stage-individual-episodes", response_model=None)
async def stage_individual_episode_releases(
    request_id: int,
    season_number: int,
    http_request: FastAPIRequest,
    redirect_to: str | None = Form(default=None),
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse | JSONResponse:
    """Stage the highest-scored stored release for each episode in a TV season."""
    request = await load_request_or_404(db, request_id)
    if request.media_type != MediaType.TV:
        raise HTTPException(
            status_code=400, detail="Stage individual episodes is only available for TV requests"
        )

    episode_result = await db.execute(
        select(Episode.episode_number)
        .join(Season, Episode.season_id == Season.id)
        .where(Season.request_id == request_id, Season.season_number == season_number)
        .order_by(Episode.episode_number.asc())
    )
    episode_numbers = list(episode_result.scalars().all())
    if not episode_numbers:
        raise HTTPException(status_code=404, detail="Season not found")

    release_result = await db.execute(
        select(Release)
        .where(
            Release.request_id == request_id,
            Release.season_number == season_number,
            Release.episode_number.in_(episode_numbers),
            Release.passed_rules.is_(True),
            _release_has_usable_url(),
        )
        .order_by(Release.episode_number.asc(), Release.score.desc(), Release.seeders.desc())
    )
    releases_by_episode: dict[int, Release] = {}
    for release in release_result.scalars().all():
        if release.episode_number is not None and release.episode_number not in releases_by_episode:
            releases_by_episode[release.episode_number] = release

    missing = [episode for episode in episode_numbers if episode not in releases_by_episode]
    if missing:
        return JSONResponse(
            {
                "status": "error",
                "message": f"No eligible releases for episode(s): {', '.join(map(str, missing))}",
            },
            status_code=400,
        )

    result = await StagingService(db).stage_individual_episode_releases(
        request,
        season_number,
        [releases_by_episode[episode] for episode in episode_numbers],
    )
    message = _selection_success_message(result)
    if "application/json" in http_request.headers.get("accept", ""):
        return JSONResponse({"status": "ok", "message": message})
    return RedirectResponse(
        url=selection_redirect_url(redirect_to, request, prefer_staged_view=True),
        status_code=303,
    )


@router.post("/{request_id}/manual-release/use", response_model=None)
async def use_manual_release(
    request_id: int,
    http_request: FastAPIRequest,
    title: str = Form(...),
    size: int = Form(...),
    seeders: int = Form(default=0),
    leechers: int = Form(default=0),
    indexer: str = Form(...),
    download_url: str = Form(default=""),
    magnet_url: str | None = Form(default=None),
    info_hash: str | None = Form(default=None),
    publish_date: str | None = Form(default=None),
    resolution: str | None = Form(default=None),
    codec: str | None = Form(default=None),
    release_group: str | None = Form(default=None),
    uploaded_by: str | None = Form(default=None),
    redirect_to: str | None = Form(default=None),
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse | JSONResponse:
    """Persist and use an ad hoc manual-search release for a request."""
    request = await load_request_or_404(db, request_id)

    publish_dt = None
    if publish_date:
        try:
            publish_dt = datetime.fromisoformat(publish_date.replace("Z", "+00:00"))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid publish_date") from exc

    release = ProwlarrRelease(
        title=title,
        size=size,
        seeders=seeders,
        leechers=leechers,
        download_url=download_url,
        magnet_url=magnet_url,
        info_hash=info_hash,
        indexer=indexer,
        publish_date=publish_dt,
        resolution=resolution,
        codec=codec,
        release_group=release_group,
        uploaded_by=uploaded_by,
    )

    service = SearchService(db)
    result = await service.select_manual_release(request, release)
    if "application/json" in http_request.headers.get("accept", ""):
        return JSONResponse(
            {
                "status": "ok",
                "message": _selection_success_message(result),
            }
        )
    return RedirectResponse(
        url=selection_redirect_url(
            redirect_to,
            request,
            prefer_staged_view=result.get("status") == "staged",
        ),
        status_code=303,
    )


@router.post("/{request_id}/deny", response_model=None)
async def deny_request(
    request_id: int,
    http_request: FastAPIRequest,
    redirect_to: str | None = Form(default=None),
    reason: str | None = Form(default=None),
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse | JSONResponse:
    """Decline a request in Overseerr and mark as denied."""
    request = await load_request_or_404(db, request_id)

    await _deny_request_record(request, db, reason=reason)
    redirect_url = redirect_to or "/"
    if "application/json" in http_request.headers.get("accept", ""):
        return _json_action_response("Request denied", redirect_url)
    return RedirectResponse(url=redirect_url, status_code=303)


@router.post("/{request_id}/episodes/{episode_id}/mark-available")
async def mark_episode_available(
    request_id: int,
    episode_id: int,
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Mark a single episode as available and recalculate season/request status.

    For TV requests, season and request statuses are derived from episodes
    (not persisted directly) since episode status is ground truth.
    """
    result = await db.execute(select(Episode).where(Episode.id == episode_id))
    episode = result.scalar_one_or_none()
    if not episode:
        raise HTTPException(status_code=404, detail="Episode not found")

    # Load season with episodes
    season_result = await db.execute(select(Season).where(Season.id == episode.season_id))
    season = season_result.scalar_one_or_none()
    if not season or season.request_id != request_id:
        raise HTTPException(status_code=404, detail="Episode does not belong to this request")

    if episode.status == RequestStatus.COMPLETED:
        raise HTTPException(status_code=400, detail="Episode is already completed")

    episode.status = RequestStatus.COMPLETED

    # Load request with seasons
    req_result = await db.execute(select(RequestModel).where(RequestModel.id == request_id))
    request = req_result.scalar_one_or_none()
    if not request:
        raise HTTPException(status_code=404, detail="Request not found")
    await db.refresh(request, ["seasons"])

    if request.media_type == MediaType.TV:
        # Derive season status from episodes (don't write for TV)
        await db.refresh(season, ["episodes"])
        _ = derive_season_status(list(season.episodes))
        # Derive request status from all episodes
        all_episodes: list[Episode] = []
        for s in request.seasons:
            await db.refresh(s, ["episodes"])
            all_episodes.extend(s.episodes)
        _ = derive_request_status_from_episodes(all_episodes)
    else:
        # Movie: persist season and request status directly
        await db.refresh(season, ["episodes"])
        season.status = derive_season_status(list(season.episodes))
        all_episodes = list(season.episodes)
        request.status = derive_request_status_from_episodes(all_episodes)

    activity_log = ActivityLogService(db)
    await activity_log.log(
        EventType.EPISODE_MARKED_AVAILABLE,
        request_id=request_id,
        details={"episode_id": episode_id, "season_id": season.id},
    )

    await db.commit()
    return JSONResponse({"status": "ok"})


@router.post("/{request_id}/seasons/{season_id}/mark-all-available")
async def mark_season_all_available(
    request_id: int,
    season_id: int,
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Mark all episodes in a season as available and recalculate statuses.

    For TV requests, season and request statuses are derived from episodes
    (not persisted directly) since episode status is ground truth.
    """
    season_result = await db.execute(select(Season).where(Season.id == season_id))
    season = season_result.scalar_one_or_none()
    if not season or season.request_id != request_id:
        raise HTTPException(
            status_code=404, detail="Season not found or does not belong to this request"
        )

    await db.refresh(season, ["episodes"])

    activity_log = ActivityLogService(db)
    for ep in season.episodes:
        if ep.status != RequestStatus.COMPLETED:
            ep.status = RequestStatus.COMPLETED
            await activity_log.log(
                EventType.EPISODE_MARKED_AVAILABLE,
                request_id=request_id,
                details={"episode_id": ep.id, "season_id": season_id},
            )

    req_result = await db.execute(select(RequestModel).where(RequestModel.id == request_id))
    request = req_result.scalar_one_or_none()
    if not request:
        raise HTTPException(status_code=404, detail="Request not found")
    await db.refresh(request, ["seasons"])

    if request.media_type == MediaType.TV:
        # Derive season status from episodes (don't persist for TV)
        _ = derive_season_status(list(season.episodes))
        # Derive request status from all episodes across all seasons
        all_episodes: list[Episode] = []
        for s in request.seasons:
            await db.refresh(s, ["episodes"])
            all_episodes.extend(s.episodes)
        _ = derive_request_status_from_episodes(all_episodes)
    else:
        season.status = derive_season_status(list(season.episodes))
        all_episodes = list(season.episodes)
        request.status = derive_request_status_from_episodes(all_episodes)

    await db.commit()
    return JSONResponse({"status": "ok"})
