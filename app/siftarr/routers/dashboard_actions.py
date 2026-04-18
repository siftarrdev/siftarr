"""Dashboard form-POST actions router for request lifecycle operations."""

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, Form, HTTPException
from fastapi import Request as FastAPIRequest
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.siftarr.database import get_db
from app.siftarr.models.request import MediaType
from app.siftarr.models.request import Request as RequestModel
from app.siftarr.models.rule import Rule
from app.siftarr.services.lifecycle_service import LifecycleService
from app.siftarr.services.media_helpers import extract_media_title_and_year
from app.siftarr.services.overseerr_service import OverseerrService
from app.siftarr.services.pending_queue_service import PendingQueueService
from app.siftarr.services.prowlarr_service import ProwlarrRelease, ProwlarrService
from app.siftarr.services.qbittorrent_service import QbittorrentService
from app.siftarr.services.release_selection_service import (
    persist_manual_release,
    use_releases,
)
from app.siftarr.services.request_service import (
    bulk_redirect_url,
    load_request_or_404,
    selection_redirect_url,
)
from app.siftarr.services.rule_engine import ReleaseEvaluation, RuleEngine
from app.siftarr.services.runtime_settings import get_effective_settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/requests", tags=["dashboard-actions"])


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
    runtime_settings = await get_effective_settings(db)

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
    if result.get("status") == "completed":
        await queue_service.remove_from_queue(request.id)

    return result


async def _deny_request_record(
    request: RequestModel,
    db: AsyncSession,
    reason: str | None = None,
) -> None:
    """Decline a request in Overseerr and mark it denied locally."""
    effective_settings = await get_effective_settings(db)
    overseerr_service = OverseerrService(settings=effective_settings)
    lifecycle_service = LifecycleService(db)
    queue_service = PendingQueueService(db)

    try:
        if request.overseerr_request_id:
            await overseerr_service.decline_request(request.overseerr_request_id, reason=reason)

        await queue_service.remove_from_queue(request.id)
        await lifecycle_service.mark_as_denied(request.id, reason=reason)
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

    await use_releases(db, request, [release], selection_source="manual")
    if "application/json" in http_request.headers.get("accept", ""):
        return JSONResponse({"status": "ok", "message": "Torrent staged successfully"})
    return RedirectResponse(
        url=selection_redirect_url(redirect_to, request),
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

    await _select_manual_release_for_request(db, request, release)
    if "application/json" in http_request.headers.get("accept", ""):
        return JSONResponse({"status": "ok", "message": "Torrent staged successfully"})
    return RedirectResponse(
        url=selection_redirect_url(redirect_to, request),
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
