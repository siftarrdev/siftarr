"""Dashboard form-POST actions router for request lifecycle operations."""

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, Form, HTTPException
from fastapi import Request as FastAPIRequest
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.siftarr.config import get_settings
from app.siftarr.database import get_db
from app.siftarr.models import EventType
from app.siftarr.models.episode import Episode
from app.siftarr.models.request import MediaType, RequestStatus
from app.siftarr.models.request import Request as RequestModel
from app.siftarr.models.rule import Rule
from app.siftarr.models.season import Season
from app.siftarr.services.activity_log_service import ActivityLogService
from app.siftarr.services.lifecycle_service import LifecycleService
from app.siftarr.services.media_helpers import extract_media_title_and_year
from app.siftarr.services.overseerr_service import OverseerrService
from app.siftarr.services.pending_queue_service import PendingQueueService
from app.siftarr.services.prowlarr_service import ProwlarrRelease, ProwlarrService
from app.siftarr.services.qbittorrent_service import QbittorrentService
from app.siftarr.services.release_storage import persist_manual_release
from app.siftarr.services.staging_actions import use_releases
from app.siftarr.services.request_service import (
    bulk_redirect_url,
    load_request_or_404,
    selection_redirect_url,
)
from app.siftarr.services.rule_engine import ReleaseEvaluation, RuleEngine

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/requests", tags=["dashboard-actions"])


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


async def _evaluate_manual_release_for_request(
    db: AsyncSession,
    request: RequestModel,
    release: ProwlarrRelease,
) -> ReleaseEvaluation:
    """Evaluate an ad hoc release using the request media type rules."""
    rules_result = await db.execute(select(Rule))
    rules = list(rules_result.scalars().all())
    engine = RuleEngine.from_db_rules(rules=rules, media_type=request.media_type.value)
    return engine.evaluate(release)


async def _select_manual_release_for_request(
    db: AsyncSession,
    request: RequestModel,
    release: ProwlarrRelease,
) -> dict[str, object]:
    """Persist and use a manual-search release through the normal selection path."""
    evaluation = await _evaluate_manual_release_for_request(db, request, release)
    stored_release = await persist_manual_release(db, request, release, evaluation)
    return await use_releases(db, request, [stored_release], selection_source="manual")


async def _process_request_search(
    request: RequestModel,
    db: AsyncSession,
) -> dict:
    """Run torrent search for a request and clean up queue state on success."""
    activity_log = ActivityLogService(db)
    await activity_log.log(
        EventType.SEARCH_STARTED,
        request_id=request.id,
        details={"title": request.title, "media_type": request.media_type.value},
    )

    runtime_settings = get_settings()

    # Backfill year if missing (e.g. Overseerr was unreachable at creation time)
    if request.year is None and (request.tmdb_id or request.tvdb_id):
        overseerr = OverseerrService(settings=runtime_settings)
        try:
            media_type_for_api = "movie" if request.media_type == MediaType.MOVIE else "tv"
            media_id = request.tmdb_id or request.tvdb_id
            if media_id is None:
                return {}
            _, year = await extract_media_title_and_year(overseerr, media_type_for_api, media_id)
            if year is not None:
                lifecycle = LifecycleService(db)
                await lifecycle.update_request_metadata(request.id, year=year)
                await db.refresh(request)
        except Exception:
            pass
        finally:
            await overseerr.close()

    prowlarr_service = ProwlarrService(settings=runtime_settings)
    qbittorrent_service = QbittorrentService(settings=runtime_settings)
    queue_service = PendingQueueService(db)

    if request.media_type.value == "movie":
        from app.siftarr.services.movie_decision_service import MovieDecisionService

        decision_service = MovieDecisionService(db, prowlarr_service, qbittorrent_service)
    else:
        from app.siftarr.services.tv_decision_service import TVDecisionService

        decision_service = TVDecisionService(db, prowlarr_service, qbittorrent_service)

    result = await decision_service.process_request(request.id)

    activity_log = ActivityLogService(db)
    await activity_log.log(
        EventType.SEARCH_COMPLETED,
        request_id=request.id,
        details={
            "status": result.get("status"),
            "message": result.get("message"),
        },
    )

    if result.get("status") == "completed":
        await queue_service.remove_from_queue(request.id)

    return result


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

    try:
        if request.overseerr_request_id:
            await overseerr_service.decline_request(request.overseerr_request_id, reason=reason)

        await queue_service.remove_from_queue(request.id)
        await lifecycle_service.transition(request.id, RequestStatus.DENIED, reason=reason)
    finally:
        await overseerr_service.close()


@router.post("/{request_id}/search")
async def search_request_now(
    request_id: int,
    redirect_to: str | None = Form(default=None),
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    """Trigger a manual torrent search for a request."""
    request = await load_request_or_404(db, request_id)

    await _process_request_search(request, db)
    return RedirectResponse(url=redirect_to or "/?tab=pending", status_code=303)


@router.post("/bulk")
async def bulk_request_action(
    action: str = Form(...),
    request_ids: list[int] = Form(default=[]),
    redirect_to: str | None = Form(default=None),
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    """Apply a bulk action to selected requests."""
    redirect_url = bulk_redirect_url(redirect_to)
    if not request_ids:
        return RedirectResponse(url=redirect_url, status_code=303)

    result = await db.execute(
        select(RequestModel)
        .where(RequestModel.id.in_(request_ids))
        .order_by(RequestModel.created_at.desc())
    )
    requests = list(result.scalars().all())

    for request in requests:
        if action == "search":
            await _process_request_search(request, db)
        elif action == "deny":
            await _deny_request_record(request, db, reason="Bulk denied")

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

    from app.siftarr.models.release import Release

    release_result = await db.execute(
        select(Release).where(Release.id == release_id, Release.request_id == request_id)
    )
    release = release_result.scalar_one_or_none()
    if not release:
        raise HTTPException(status_code=404, detail="Release not found")

    result = await use_releases(db, request, [release], selection_source="manual")
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
    )

    result = await _select_manual_release_for_request(db, request, release)
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


@router.post("/{request_id}/deny")
async def deny_request(
    request_id: int,
    redirect_to: str | None = Form(default=None),
    reason: str | None = Form(default=None),
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    """Decline a request in Overseerr and mark as denied."""
    request = await load_request_or_404(db, request_id)

    await _deny_request_record(request, db, reason=reason)
    return RedirectResponse(url=redirect_to or "/", status_code=303)


def _recalculate_season_status(season: Season) -> RequestStatus:
    """Compute season status from its episodes."""
    if not season.episodes:
        return season.status
    statuses = {ep.status for ep in season.episodes}
    if statuses <= {RequestStatus.COMPLETED}:
        return RequestStatus.COMPLETED
    if statuses & {RequestStatus.COMPLETED}:
        return RequestStatus.PENDING
    return season.status


def _recalculate_request_status(request: RequestModel) -> RequestStatus:
    """Compute request status from its seasons."""
    if not request.seasons:
        return request.status
    season_statuses = {s.status for s in request.seasons}
    if season_statuses <= {RequestStatus.COMPLETED}:
        return RequestStatus.COMPLETED
    if season_statuses & {RequestStatus.COMPLETED, RequestStatus.PENDING}:
        return RequestStatus.PENDING
    return request.status


@router.post("/{request_id}/episodes/{episode_id}/mark-available")
async def mark_episode_available(
    request_id: int,
    episode_id: int,
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Mark a single episode as available and recalculate season/request status."""
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

    # Eagerly load all episodes for recalculation
    await db.refresh(season, ["episodes"])
    season.status = _recalculate_season_status(season)

    # Load request with seasons
    req_result = await db.execute(select(RequestModel).where(RequestModel.id == request_id))
    request = req_result.scalar_one_or_none()
    if not request:
        raise HTTPException(status_code=404, detail="Request not found")
    await db.refresh(request, ["seasons"])
    request.status = _recalculate_request_status(request)

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
    """Mark all episodes in a season as available and recalculate statuses."""
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

    season.status = _recalculate_season_status(season)

    req_result = await db.execute(select(RequestModel).where(RequestModel.id == request_id))
    request = req_result.scalar_one_or_none()
    if not request:
        raise HTTPException(status_code=404, detail="Request not found")
    await db.refresh(request, ["seasons"])
    request.status = _recalculate_request_status(request)

    await db.commit()
    return JSONResponse({"status": "ok"})
