"""Dashboard router for main UI."""

import asyncio

from fastapi import APIRouter, Depends, Form, HTTPException
from fastapi import Request as FastAPIRequest
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.arbitratarr.database import get_db
from app.arbitratarr.models.request import Request as RequestModel
from app.arbitratarr.models.request import RequestStatus
from app.arbitratarr.models.rule import Rule
from app.arbitratarr.models.staged_torrent import StagedTorrent
from app.arbitratarr.services.lifecycle_service import LifecycleService
from app.arbitratarr.services.overseerr_service import OverseerrService
from app.arbitratarr.services.pending_queue_service import PendingQueueService
from app.arbitratarr.services.prowlarr_service import ProwlarrRelease, ProwlarrService
from app.arbitratarr.services.qbittorrent_service import QbittorrentService
from app.arbitratarr.services.rule_engine import RuleEngine
from app.arbitratarr.services.runtime_settings import get_effective_settings

router = APIRouter(tags=["dashboard"])
templates = Jinja2Templates(directory="app/arbitratarr/templates")


def _build_poster_url(overseerr_url: str | None, poster_path: object) -> str | None:
    """Build a usable poster URL from Overseerr or TMDB-style paths."""
    if not poster_path:
        return None

    poster = str(poster_path).strip()
    if not poster:
        return None

    if poster.startswith(("http://", "https://")):
        return poster

    base_url = str(overseerr_url or "").rstrip("/")
    if poster.startswith("/images/"):
        return f"{base_url}{poster}" if base_url else None

    if poster.startswith("/"):
        if base_url:
            return f"{base_url}/images/original{poster}"
        return f"https://image.tmdb.org/t/p/original{poster}"

    return poster


def _choose_overseerr_display_status(request_status: str, media_status: str) -> str:
    """Choose the most useful Overseerr status label for UI display."""
    if media_status in {"processing", "partially_available", "available", "deleted"}:
        return media_status
    if request_status not in {"unknown", "no_overseerr_id"}:
        return request_status
    if media_status != "unknown":
        return media_status
    return request_status


async def _process_request_search(
    request: RequestModel,
    db: AsyncSession,
) -> dict:
    """Run torrent search for a request and clean up queue state on success."""
    runtime_settings = await get_effective_settings(db)
    prowlarr_service = ProwlarrService(settings=runtime_settings)
    qbittorrent_service = QbittorrentService(settings=runtime_settings)
    queue_service = PendingQueueService(db)

    if request.media_type.value == "movie":
        from app.arbitratarr.services.movie_decision_service import MovieDecisionService

        decision_service = MovieDecisionService(db, prowlarr_service, qbittorrent_service)
    else:
        from app.arbitratarr.services.tv_decision_service import TVDecisionService

        decision_service = TVDecisionService(db, prowlarr_service, qbittorrent_service)

    result = await decision_service.process_request(request.id)
    if result.get("status") == "completed":
        await queue_service.remove_from_queue(request.id)

    return result


async def _approve_and_search_request(
    request: RequestModel,
    db: AsyncSession,
) -> bool:
    """Approve a request in Overseerr when needed, then trigger search."""
    effective_settings = await get_effective_settings(db)
    overseerr_service = OverseerrService(settings=effective_settings)

    try:
        if request.overseerr_request_id:
            success = await overseerr_service.approve_request(request.overseerr_request_id)
            if not success:
                return False

        await _process_request_search(request, db)
        return True
    finally:
        await overseerr_service.close()


async def _deny_request_record(
    request: RequestModel,
    db: AsyncSession,
) -> None:
    """Decline a request in Overseerr and mark it failed locally."""
    effective_settings = await get_effective_settings(db)
    overseerr_service = OverseerrService(settings=effective_settings)
    lifecycle_service = LifecycleService(db)
    queue_service = PendingQueueService(db)

    try:
        if request.overseerr_request_id:
            await overseerr_service.decline_request(request.overseerr_request_id)

        await queue_service.remove_from_queue(request.id)
        await lifecycle_service.mark_as_failed(request.id)
    finally:
        await overseerr_service.close()


def _get_bulk_redirect_url(redirect_to: str | None) -> str:
    """Return the target tab after a bulk action completes."""
    return redirect_to or "/?tab=pending"


@router.get("/")
async def dashboard(
    request: FastAPIRequest,
    db: AsyncSession = Depends(get_db),
):
    """Display main dashboard."""
    lifecycle_service = LifecycleService(db)
    queue_service = PendingQueueService(db)
    effective_settings = await get_effective_settings(db)
    overseerr_service = OverseerrService(settings=effective_settings)

    # Get active requests
    active_requests = await lifecycle_service.get_active_requests(limit=500)

    # Fetch Overseerr statuses concurrently for all requests with overseerr_request_id
    overseerr_statuses: dict[int, str] = {}
    overseerr_request_statuses: dict[int, str] = {}
    overseerr_media_statuses: dict[int, str] = {}

    async def _fetch_status(req_obj: RequestModel) -> tuple[int, str, str, str]:
        if not req_obj.overseerr_request_id:
            return req_obj.id, "no_overseerr_id", "no_overseerr_id", "unknown"
        try:
            ov_status = await overseerr_service.get_request_status(req_obj.overseerr_request_id)
            if ov_status and isinstance(ov_status, dict):
                media = ov_status.get("media") or {}
                request_status = overseerr_service.normalize_request_status(ov_status.get("status"))
                media_status = overseerr_service.normalize_media_status(media.get("status"))
                return (
                    req_obj.id,
                    _choose_overseerr_display_status(request_status, media_status),
                    request_status,
                    media_status,
                )
            return req_obj.id, "unknown", "unknown", "unknown"
        except Exception:
            return req_obj.id, "unknown", "unknown", "unknown"

    status_results = await asyncio.gather(*[_fetch_status(req) for req in active_requests])
    for req_id, status, request_status, media_status in status_results:
        overseerr_statuses[req_id] = status
        overseerr_request_statuses[req_id] = request_status
        overseerr_media_statuses[req_id] = media_status

    await overseerr_service.close()

    # Active tab shows all active requests.
    filtered_requests = active_requests

    # Pending search shows only local pending requests that Overseerr has approved
    # or that are partially available and still need search action.
    pending_requests = [
        req
        for req in active_requests
        if req.status == RequestStatus.PENDING
        and req.overseerr_request_id
        and (
            overseerr_request_statuses.get(req.id) == "approved"
            or overseerr_media_statuses.get(req.id) == "partially_available"
        )
    ]

    # Get pending items and pending requests
    pending_items = await queue_service.get_all_pending()
    pending_items_by_request_id = {item.request_id: item for item in pending_items}

    # Get selected torrents that are either waiting in staging or already sent to qBittorrent.
    result = await db.execute(
        select(StagedTorrent)
        .where(StagedTorrent.status.in_(["staged", "approved"]))
        .order_by(StagedTorrent.created_at.desc())
    )
    staged_torrents = list(result.scalars().all())

    staged_request_ids = {
        torrent.request_id for torrent in staged_torrents if torrent.request_id is not None
    }
    staged_request_statuses: dict[int, str] = {}
    if staged_request_ids:
        staged_request_result = await db.execute(
            select(RequestModel.id, RequestModel.status).where(
                RequestModel.id.in_(staged_request_ids)
            )
        )
        staged_request_statuses = {
            request_id: status.value for request_id, status in staged_request_result.all()
        }

    # Get stats
    stats = await lifecycle_service.get_requests_stats()

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "request": request,
            "active_requests": filtered_requests,
            "overseerr_statuses": overseerr_statuses,
            "overseerr_request_statuses": overseerr_request_statuses,
            "overseerr_media_statuses": overseerr_media_statuses,
            "overseerr_url": str(effective_settings.overseerr_url or "").rstrip("/"),
            "pending_requests": pending_requests,
            "pending_items_by_request_id": pending_items_by_request_id,
            "staged_torrents": staged_torrents,
            "staged_request_statuses": staged_request_statuses,
            "stats": {
                "active": len(active_requests),
                "pending": len(pending_requests),
                "staged": len(staged_torrents),
                "completed": stats["by_status"].get(RequestStatus.COMPLETED.value, 0),
            },
        },
    )


@router.post("/requests/{request_id}/approve")
async def approve_request(
    request_id: int,
    redirect_to: str | None = Form(default=None),
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    """Approve a request in Overseerr and trigger search."""
    result = await db.execute(select(RequestModel).where(RequestModel.id == request_id))
    request = result.scalar_one_or_none()

    if not request:
        raise HTTPException(status_code=404, detail="Request not found")

    await _approve_and_search_request(request, db)
    return RedirectResponse(url=redirect_to or "/", status_code=303)


@router.post("/requests/{request_id}/search")
async def search_request_now(
    request_id: int,
    redirect_to: str | None = Form(default=None),
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    """Trigger a manual torrent search for a request."""
    result = await db.execute(select(RequestModel).where(RequestModel.id == request_id))
    request = result.scalar_one_or_none()

    if not request:
        raise HTTPException(status_code=404, detail="Request not found")

    await _process_request_search(request, db)
    return RedirectResponse(url=redirect_to or "/?tab=pending", status_code=303)


@router.post("/requests/bulk")
async def bulk_request_action(
    action: str = Form(...),
    request_ids: list[int] = Form(default=[]),
    redirect_to: str | None = Form(default=None),
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    """Apply a bulk action to selected requests."""
    redirect_url = _get_bulk_redirect_url(redirect_to)
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
        elif action == "approve":
            await _approve_and_search_request(request, db)
        elif action == "reject":
            await _deny_request_record(request, db)

    return RedirectResponse(url=redirect_url, status_code=303)


@router.get("/requests/{request_id}/details")
async def request_details(
    request_id: int,
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    result = await db.execute(select(RequestModel).where(RequestModel.id == request_id))
    request = result.scalar_one_or_none()
    if not request:
        raise HTTPException(status_code=404, detail="Request not found")

    effective_settings = await get_effective_settings(db)
    overseerr_service = OverseerrService(settings=effective_settings)
    details: dict[str, object] = {"request": {"id": request.id, "title": request.title}}

    try:
        if request.overseerr_request_id:
            ov = await overseerr_service.get_request(request.overseerr_request_id)
            media: dict[str, object] = {}
            request_status = "unknown"
            if ov:
                media = ov.get("media") or {}
                request_status = overseerr_service.normalize_media_status(media.get("status"))

            media_details = None
            if request.media_type.value == "movie" and request.tmdb_id:
                media_details = await overseerr_service.get_media_details("movie", request.tmdb_id)
            elif request.media_type.value == "tv":
                tv_external_id = request.tmdb_id or request.tvdb_id
                if tv_external_id:
                    media_details = await overseerr_service.get_media_details("tv", tv_external_id)

            merged_media = {**media, **(media_details or {})}
            poster = _build_poster_url(
                effective_settings.overseerr_url,
                merged_media.get("posterPath") or merged_media.get("poster"),
            )

            details["overseerr"] = {
                "overview": merged_media.get("overview") or merged_media.get("summary") or "",
                "poster": poster,
                "status": request_status,
            }
    finally:
        await overseerr_service.close()

    from app.arbitratarr.models.release import Release

    release_result = await db.execute(select(Release).where(Release.request_id == request_id))
    releases = list(release_result.scalars().all())
    rules = await db.execute(select(Rule))
    rule_list = list(rules.scalars().all())
    engine = RuleEngine.from_db_rules(rules=rule_list, media_type=request.media_type.value)

    matched = []
    for release in releases:
        evaluation = engine.evaluate(
            ProwlarrRelease(
                title=release.title,
                size=release.size,
                seeders=release.seeders,
                leechers=release.leechers,
                download_url=release.download_url,
                magnet_url=release.magnet_url,
                info_hash=release.info_hash,
                indexer=release.indexer,
                publish_date=release.publish_date,
                resolution=release.resolution,
                codec=release.codec,
                release_group=release.release_group,
            )
        )
        matched.append(
            {
                "title": release.title,
                "score": release.score,
                "passed": release.passed_rules,
                "matches": [
                    {
                        "rule_name": m.rule_name,
                        "matched": m.matched,
                        "score_delta": m.score_delta,
                    }
                    for m in evaluation.matches
                ],
            }
        )

    details["releases"] = matched
    return JSONResponse(details)


@router.post("/requests/{request_id}/deny")
async def deny_request(
    request_id: int,
    redirect_to: str | None = Form(default=None),
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    """Decline a request in Overseerr and mark as failed."""
    result = await db.execute(select(RequestModel).where(RequestModel.id == request_id))
    request = result.scalar_one_or_none()

    if not request:
        raise HTTPException(status_code=404, detail="Request not found")

    await _deny_request_record(request, db)
    return RedirectResponse(url=redirect_to or "/", status_code=303)
