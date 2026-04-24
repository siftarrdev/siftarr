"""Dashboard router for main UI."""

import logging
from datetime import UTC, date, datetime, timedelta

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi import Request as FastAPIRequest
from fastapi.responses import Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.siftarr.config import get_settings
from app.siftarr.database import get_db
from app.siftarr.models.episode import Episode
from app.siftarr.models.request import (
    MediaType,
    RequestStatus,
    is_active_staging_workflow_status,
)
from app.siftarr.models.request import Request as RequestModel
from app.siftarr.models.season import Season
from app.siftarr.models.staged_torrent import StagedTorrent
from app.siftarr.services.http_client import get_shared_client
from app.siftarr.services.lifecycle_service import LifecycleService
from app.siftarr.services.overseerr_service import OverseerrService
from app.siftarr.services.tv_details_service import load_tv_seasons_with_episodes

logger = logging.getLogger(__name__)

router = APIRouter(tags=["dashboard"])
templates = Jinja2Templates(directory="app/siftarr/templates")


@router.get("/")
async def dashboard(
    request: FastAPIRequest,
    db: AsyncSession = Depends(get_db),
):
    """Display main dashboard."""
    lifecycle_service = LifecycleService(db)
    effective_settings = get_settings()

    # Get active requests
    active_requests = await lifecycle_service.get_active_requests(limit=500)

    async def _tv_has_pending_episodes(request_id: int) -> bool:
        seasons, episodes = await load_tv_seasons_with_episodes(db, request_id)
        if not seasons or not episodes:
            return False
        return any(episode.status == RequestStatus.PENDING for episode in episodes)

    async def _should_show_in_unreleased(req: RequestModel) -> bool:
        if req.status == RequestStatus.UNRELEASED:
            return True
        if req.media_type != MediaType.TV or req.status not in {
            RequestStatus.PENDING,
            RequestStatus.COMPLETED,
        }:
            return False

        seasons, episodes = await load_tv_seasons_with_episodes(db, req.id)
        if not seasons or not episodes:
            return False

        statuses = {episode.status for episode in episodes}
        return RequestStatus.UNRELEASED in statuses and RequestStatus.PENDING not in statuses

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
    raw_staged_request_statuses: dict[int, RequestStatus] = {}
    staged_request_statuses: dict[int, str] = {}
    if staged_request_ids:
        staged_request_result = await db.execute(
            select(RequestModel.id, RequestModel.status).where(
                RequestModel.id.in_(staged_request_ids)
            )
        )
        for request_id, status in staged_request_result.all():
            raw_staged_request_statuses[request_id] = status
        staged_request_statuses = {
            request_id: status.value for request_id, status in raw_staged_request_statuses.items()
        }

    # Only keep request-linked torrents while the linked request is still
    # actively staged or downloading.
    staged_torrents = [
        t
        for t in staged_torrents
        if t.request_id is None
        or is_active_staging_workflow_status(raw_staged_request_statuses.get(t.request_id))
    ]

    # Build mapping for replaced torrents to their replacements
    replaced_by_titles: dict[int, str] = {}
    replaced_ids = [t.replaced_by_id for t in staged_torrents if t.replaced_by_id]
    if replaced_ids:
        replaced_result = await db.execute(
            select(StagedTorrent.id, StagedTorrent.title).where(StagedTorrent.id.in_(replaced_ids))
        )
        replaced_by_titles = {row[0]: row[1] for row in replaced_result.all()}

    # Get completed requests for the Finished tab
    completed_requests = await lifecycle_service.get_requests_by_status(
        RequestStatus.COMPLETED, limit=500
    )

    unreleased_candidates = await lifecycle_service.get_unreleased_requests(limit=500)
    unreleased_candidate_by_id = {
        req.id: req for req in [*active_requests, *completed_requests, *unreleased_candidates]
    }
    unreleased_requests = [
        req for req in unreleased_candidate_by_id.values() if await _should_show_in_unreleased(req)
    ]
    unreleased_request_ids = {req.id for req in unreleased_requests}
    filtered_requests = [req for req in active_requests if req.id not in unreleased_request_ids]
    completed_requests = [req for req in completed_requests if req.id not in unreleased_request_ids]

    # Pending search shows PENDING and SEARCHING requests, plus TV requests that
    # have pending episodes alongside partial availability.
    pending_requests = []
    for req in filtered_requests:
        if req.status in (RequestStatus.PENDING, RequestStatus.SEARCHING):
            pending_requests.append(req)
            continue
        if (
            req.status == RequestStatus.PENDING
            and req.media_type == MediaType.TV
            and await _tv_has_pending_episodes(req.id)
        ):
            pending_requests.append(req)
    overseerr_service = OverseerrService(settings=effective_settings)

    async def _earliest_future_release(req_obj: RequestModel) -> tuple[int, str | None]:
        try:
            today = date.today()
            if req_obj.media_type == MediaType.MOVIE:
                if not req_obj.tmdb_id:
                    return req_obj.id, None
                details = await overseerr_service.get_media_details("movie", req_obj.tmdb_id)
                if not details:
                    return req_obj.id, None
                candidates: list[date] = []
                release_date_str = details.get("releaseDate")
                if release_date_str:
                    try:
                        parsed = date.fromisoformat(str(release_date_str)[:10])
                        if parsed > today:
                            candidates.append(parsed)
                    except ValueError:
                        pass
                releases_block = details.get("releases") or {}
                for result in releases_block.get("results") or []:
                    for rd in result.get("release_dates") or []:
                        if rd.get("type") not in {3, 4, 5}:
                            continue
                        raw = rd.get("release_date")
                        if not raw:
                            continue
                        try:
                            parsed = date.fromisoformat(str(raw)[:10])
                        except ValueError:
                            continue
                        if parsed > today:
                            candidates.append(parsed)
                if not candidates:
                    return req_obj.id, None
                return req_obj.id, min(candidates).isoformat()

            external_id = req_obj.tmdb_id or req_obj.tvdb_id
            if external_id:
                details = await overseerr_service.get_media_details("tv", external_id)
                if details:
                    next_ep = details.get("nextEpisodeToAir") or {}
                    air_date_str = next_ep.get("airDate")
                    if air_date_str:
                        try:
                            parsed = date.fromisoformat(str(air_date_str)[:10])
                            return req_obj.id, parsed.isoformat()
                        except ValueError:
                            pass

            result = await db.execute(
                select(Episode.air_date)
                .join(Season, Season.id == Episode.season_id)
                .where(Season.request_id == req_obj.id)
                .where(Episode.air_date.is_not(None))
                .where(Episode.air_date > today)
                .order_by(Episode.air_date.asc())
                .limit(1)
            )
            row = result.first()
            if row and row[0]:
                return req_obj.id, row[0].isoformat()
            return req_obj.id, None
        except Exception:
            logger.debug(
                "Failed to compute earliest future release for request %s",
                req_obj.id,
                exc_info=True,
            )
            return req_obj.id, None

    unreleased_earliest: dict[int, str | None] = {}
    if unreleased_requests:
        earliest_results = [await _earliest_future_release(req) for req in unreleased_requests]
        for req_id, iso_date in earliest_results:
            unreleased_earliest[req_id] = iso_date

    denied_cutoff = datetime.now(UTC) - timedelta(days=30)
    denied_result = await db.execute(
        select(RequestModel)
        .where(
            RequestModel.status == RequestStatus.DENIED,
            RequestModel.updated_at >= denied_cutoff,
        )
        .order_by(RequestModel.updated_at.desc())
        .limit(500)
    )
    denied_requests = list(denied_result.scalars().all())

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "request": request,
            "active_requests": filtered_requests,
            "overseerr_url": str(effective_settings.overseerr_url or "").rstrip("/"),
            "staging_mode_enabled": effective_settings.staging_mode_enabled,
            "qbittorrent_url": str(effective_settings.qbittorrent_url or "").rstrip("/"),
            "pending_requests": pending_requests,
            "staged_torrents": staged_torrents,
            "staged_request_statuses": staged_request_statuses,
            "replaced_by_titles": replaced_by_titles,
            "unreleased_requests": unreleased_requests,
            "unreleased_earliest": unreleased_earliest,
            "completed_requests": completed_requests,
            "denied_requests": denied_requests,
            "stats": {
                "active": len(filtered_requests),
                "pending": len(pending_requests),
                "staged": len(staged_torrents),
                "completed": len(completed_requests),
                "unreleased": len(unreleased_requests),
                "denied": len(denied_requests),
            },
        },
    )


# ---------------------------------------------------------------------------
# Image proxy – fetches posters via TMDB so the browser never needs direct
# access to TMDB or to the (possibly Docker-internal) Overseerr host.
# ---------------------------------------------------------------------------

_TMDB_IMAGE_BASE = "https://image.tmdb.org/t/p"
_ALLOWED_SIZES = {"w92", "w154", "w185", "w342", "w500", "w780", "original"}


@router.get("/api/poster")
async def poster_proxy(
    path: str = Query(..., description="TMDB poster path, e.g. /abc123.jpg"),
    size: str = Query("w500", description="TMDB image size"),
) -> Response:
    """Proxy a TMDB poster image through the Siftarr backend.

    This avoids CORS / mixed-content issues and prevents leaking
    Overseerr internal hostnames to the browser.
    """
    if size not in _ALLOWED_SIZES:
        size = "w500"

    # Basic safety: the path must start with / and have no directory traversal
    if not path.startswith("/") or ".." in path:
        raise HTTPException(status_code=400, detail="Invalid poster path")

    url = f"{_TMDB_IMAGE_BASE}/{size}{path}"
    try:
        client = await get_shared_client()
        resp = await client.get(url)
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail="Failed to fetch poster from TMDB") from exc

    if resp.status_code != 200:
        raise HTTPException(
            status_code=resp.status_code,
            detail="TMDB returned an error",
        )

    content_type = resp.headers.get("content-type", "image/jpeg")
    return Response(
        content=resp.content,
        media_type=content_type,
        headers={
            "Cache-Control": "public, max-age=86400",
        },
    )
