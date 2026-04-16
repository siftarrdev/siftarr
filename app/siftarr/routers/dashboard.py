"""Dashboard router for main UI."""

import asyncio
import logging
import re
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, Query
from fastapi import Request as FastAPIRequest
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.siftarr.database import async_session_maker, get_db
from app.siftarr.models.request import MediaType, RequestStatus
from app.siftarr.models.request import Request as RequestModel
from app.siftarr.models.rule import Rule
from app.siftarr.models.staged_torrent import StagedTorrent
from app.siftarr.services.http_client import get_shared_client
from app.siftarr.services.lifecycle_service import LifecycleService
from app.siftarr.services.media_helpers import extract_media_title_and_year
from app.siftarr.services.overseerr_service import (
    OverseerrService,
    clear_media_details_cache,
    clear_status_cache,
)
from app.siftarr.services.pending_queue_service import PendingQueueService
from app.siftarr.services.plex_service import PlexService
from app.siftarr.services.prowlarr_service import ProwlarrRelease, ProwlarrService
from app.siftarr.services.qbittorrent_service import QbittorrentService
from app.siftarr.services.release_parser import (
    ParsedReleaseCoverage,
    parse_release_coverage,
    parse_stored_release_coverage,
)
from app.siftarr.services.release_selection_service import (
    build_prowlarr_release,
    persist_manual_release,
    use_releases,
)
from app.siftarr.services.rule_engine import ReleaseEvaluation, RuleEngine
from app.siftarr.services.runtime_settings import get_effective_settings

logger = logging.getLogger(__name__)

router = APIRouter(tags=["dashboard"])
templates = Jinja2Templates(directory="app/siftarr/templates")


_OVERSEERR_SEMAPHORE = asyncio.Semaphore(10)
_DETAILS_SYNC_TASKS: set[int] = set()

_SINGLE_EPISODE_RELEASE_PATTERN = re.compile(
    r"(?:^|[.()\s_]+)S(?P<season>\d{1,2})E(?P<episode>\d{1,3})(?!\d)",
    re.IGNORECASE,
)
_FOLLOWUP_EPISODE_TOKEN_PATTERN = re.compile(
    r"^[.()\s_-]*(?:E\d{1,3}|-\s*E?\d{1,3})(?!\d)",
    re.IGNORECASE,
)
_ADDITIONAL_EPISODE_TOKEN_PATTERN = re.compile(
    r"(?:^|[.()\s_-]+)E\d{1,3}(?!\d)",
    re.IGNORECASE,
)


def _normalize_optional_text(value: object) -> str | None:
    """Return a JSON-safe optional string value."""
    if value is None or isinstance(value, str):
        return value
    return None


def _normalize_float(value: object) -> float:
    """Return a safe float for sorting."""
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return 0.0
    return 0.0


def _normalize_int(value: object) -> int:
    """Return a safe int for sorting."""
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return 0
    return 0


def _coerce_int_list(value: object) -> list[int]:
    """Coerce a payload field to a list of ints."""
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, int)]


def _extract_poster_path(poster_path: object) -> str | None:
    """Extract a clean TMDB poster path from various Overseerr response formats.

    Returns a TMDB-relative path like ``/abc123.jpg`` or *None*.
    """
    if not poster_path:
        return None

    poster = str(poster_path).strip()
    if not poster:
        return None

    # Already a bare TMDB path, e.g. "/kSf9svfD2WiLhrs9AP2Uih2Wq3T.jpg"
    if poster.startswith("/") and not poster.startswith("/images"):
        return poster

    # Overseerr proxied form: "/images/original/kSf9sv...jpg"
    if poster.startswith("/images/"):
        # Strip the /images/<size> prefix
        parts = poster.split("/", 3)  # ['', 'images', 'original', 'rest.jpg']
        if len(parts) >= 4:
            return f"/{parts[3]}"
        return None

    # Full URL pointing to TMDB
    if "image.tmdb.org" in poster:
        # e.g. https://image.tmdb.org/t/p/original/abc.jpg -> /abc.jpg
        idx = poster.find("/t/p/")
        if idx != -1:
            after = poster[idx + 4 :]  # "/original/abc.jpg"
            parts = after.split("/", 2)  # ['', 'original', 'abc.jpg']
            if len(parts) >= 3:
                return f"/{parts[2]}"
        return None

    # Full URL pointing to Overseerr instance – extract the TMDB portion
    if poster.startswith(("http://", "https://")) and "/images/" in poster:
        idx = poster.find("/images/")
        return _extract_poster_path(poster[idx:])

    return None


def _is_exact_single_episode_release(title: str, season_number: int, episode_number: int) -> bool:
    """Return True when the title identifies exactly one requested episode."""
    match = _SINGLE_EPISODE_RELEASE_PATTERN.search(title)
    if not match:
        return False

    if int(match.group("season")) != season_number:
        return False
    if int(match.group("episode")) != episode_number:
        return False

    remainder = title[match.end() :]
    if _FOLLOWUP_EPISODE_TOKEN_PATTERN.match(remainder):
        return False
    if _SINGLE_EPISODE_RELEASE_PATTERN.search(remainder):
        return False
    return not _ADDITIONAL_EPISODE_TOKEN_PATTERN.search(remainder)


def _build_poster_url(poster_path: object) -> str | None:
    """Build a proxied poster URL that the browser can always reach."""
    tmdb_path = _extract_poster_path(poster_path)
    if not tmdb_path:
        return None
    # URL-encode the path portion for the query parameter
    from urllib.parse import quote

    return f"/api/poster?path={quote(tmdb_path, safe='')}"


def _build_overseerr_media_url(
    overseerr_url: str | None,
    media_type: str,
    tmdb_id: int | None,
) -> str | None:
    """Build an Overseerr media URL for movie or TV pages."""
    if not overseerr_url or not tmdb_id:
        return None
    return f"{str(overseerr_url).rstrip('/')}/{media_type}/{tmdb_id}"


def _format_release_size(size_bytes: int) -> str:
    """Format bytes as a compact human-readable size."""
    if size_bytes <= 0:
        return "Unknown"
    gib = size_bytes / 1024 / 1024 / 1024
    return f"{gib:.2f} GB"


def _apply_release_size_per_season_metadata(release: dict[str, object]) -> dict[str, object]:
    """Attach derived per-season size metadata when season coverage is known."""
    size_bytes = _normalize_int(release.get("size_bytes"))
    covered_seasons = _coerce_int_list(release.get("covered_seasons"))
    known_total_seasons = _normalize_int(release.get("known_total_seasons"))
    covered_season_count = _normalize_int(release.get("covered_season_count"))

    if covered_season_count <= 0:
        if covered_seasons:
            covered_season_count = len(covered_seasons)
        elif release.get("is_complete_series") and known_total_seasons > 0:
            covered_season_count = known_total_seasons

    if size_bytes <= 0 or covered_season_count <= 0:
        release["size_per_season"] = None
        release["size_per_season_bytes"] = None
        release["size_per_season_passed"] = None
        return release

    size_per_season_bytes = int(round(size_bytes / covered_season_count))
    release["size_per_season"] = _format_release_size(size_per_season_bytes)
    release["size_per_season_bytes"] = size_per_season_bytes
    release["size_per_season_passed"] = True
    return release


def _serialize_evaluated_release(
    release: ProwlarrRelease | Any,
    evaluation: ReleaseEvaluation | Any,
    *,
    coverage: ParsedReleaseCoverage | None = None,
    known_total_seasons: int | None = None,
) -> dict[str, object]:
    """Serialize a release plus rule evaluation for dashboard responses."""
    status = "passed" if evaluation.passed else "rejected"
    payload: dict[str, object] = {
        "title": release.title,
        "_size_bytes": release.size,
        "size_bytes": release.size,
        "size": _format_release_size(release.size),
        "seeders": release.seeders,
        "leechers": release.leechers,
        "indexer": release.indexer,
        "resolution": release.resolution,
        "codec": release.codec,
        "release_group": release.release_group,
        "info_hash": release.info_hash,
        "score": evaluation.total_score,
        "passed": evaluation.passed,
        "status": status,
        "status_label": "Passed" if evaluation.passed else "Rejected",
        "rejection_reason": _normalize_optional_text(getattr(evaluation, "rejection_reason", None)),
        "download_url": release.download_url,
        "magnet_url": release.magnet_url,
        "publish_date": release.publish_date.isoformat() if release.publish_date else None,
        "stored_release_id": None,
    }

    release_id = getattr(release, "id", None)
    if release_id is not None:
        payload["id"] = release_id
        payload["stored_release_id"] = release_id

    if coverage is not None:
        covered_seasons = list(coverage.season_numbers)
        payload["covered_seasons"] = covered_seasons
        payload["covered_season_count"] = len(covered_seasons)
        payload["known_total_seasons"] = known_total_seasons
        payload["is_complete_series"] = coverage.is_complete_series
        payload["covers_all_known_seasons"] = bool(
            known_total_seasons
            and (coverage.is_complete_series or len(covered_seasons) >= known_total_seasons)
        )

    return _apply_release_size_per_season_metadata(payload)


def _dashboard_release_sort_key(release: dict[str, object]) -> tuple[float, float, int, float, str]:
    """Sort dashboard releases by score desc, size asc, then stable tie-breakers."""
    score = _normalize_float(release.get("score"))
    size_bytes = release.get("_size_bytes")
    normalized_size = (
        float(size_bytes)
        if isinstance(size_bytes, int | float) and size_bytes >= 0
        else float("inf")
    )
    seeders = _normalize_int(release.get("seeders"))
    publish_date = release.get("publish_date")
    publish_timestamp = 0.0
    if isinstance(publish_date, datetime):
        publish_timestamp = publish_date.timestamp()
    elif isinstance(publish_date, str):
        try:
            publish_timestamp = (
                datetime.fromisoformat(publish_date.replace("Z", "+00:00"))
                .astimezone(UTC)
                .timestamp()
            )
        except ValueError:
            publish_timestamp = 0.0
    title = str(release.get("title") or "").casefold()
    return (-score, normalized_size, -seeders, -publish_timestamp, title)


def _finalize_dashboard_release_payloads(
    releases: list[dict[str, object]],
) -> list[dict[str, object]]:
    """Apply shared dashboard ordering and remove internal sort metadata."""
    ordered = sorted(releases, key=_dashboard_release_sort_key)
    for release in ordered:
        release.pop("_size_bytes", None)
    return ordered


def _choose_overseerr_display_status(request_status: str, media_status: str) -> str:
    """Choose the most useful Overseerr status label for UI display."""
    if media_status in {"processing", "partially_available", "available", "deleted"}:
        return media_status
    if request_status not in {"unknown", "no_overseerr_id"}:
        return request_status
    if media_status != "unknown":
        return media_status
    return request_status


async def _load_request_record(db: AsyncSession, request_id: int) -> RequestModel:
    """Load a request or raise 404."""
    result = await db.execute(select(RequestModel).where(RequestModel.id == request_id))
    request = result.scalar_one_or_none()
    if not request:
        raise HTTPException(status_code=404, detail="Request not found")
    return request


def _selection_redirect_url(redirect_to: str | None, request: RequestModel) -> str:
    """Return a sensible redirect target after release actions."""
    if redirect_to:
        return redirect_to
    return "/?tab=pending" if request.status == RequestStatus.PENDING else "/?tab=active"


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


async def _run_background_episode_refresh(request_id: int) -> None:
    """Refresh TV details in a detached task using a fresh DB session."""
    if request_id not in _DETAILS_SYNC_TASKS:
        _DETAILS_SYNC_TASKS.add(request_id)
    try:
        async with async_session_maker() as db:
            effective_settings = await get_effective_settings(db)
            plex_service = PlexService(settings=effective_settings)
            try:
                from app.siftarr.services.episode_sync_service import EpisodeSyncService

                episode_sync = EpisodeSyncService(db, plex=plex_service)
                await episode_sync.refresh_if_stale(request_id)
            except Exception:
                logger.exception("Background episode sync failed for request_id=%s", request_id)
            finally:
                await plex_service.close()
    finally:
        _DETAILS_SYNC_TASKS.discard(request_id)


def _schedule_background_episode_refresh(
    background_tasks: BackgroundTasks | None,
    request_id: int,
) -> bool:
    """Schedule a lifecycle-managed background refresh once per request."""
    if background_tasks is None:
        return False
    if request_id in _DETAILS_SYNC_TASKS:
        return False

    _DETAILS_SYNC_TASKS.add(request_id)
    background_tasks.add_task(_run_background_episode_refresh, request_id)
    return True


def _has_unresolved_partial_tv_data(
    seasons: list[Any],
    episodes_by_season: dict[int, list[Any]],
) -> bool:
    """Return True when season rows imply Plex enrichment still needs to run."""
    for season in seasons:
        season_episodes = episodes_by_season.get(season.id, [])
        season_status = getattr(season.status, "value", season.status)
        if season_status not in {
            RequestStatus.PARTIALLY_AVAILABLE.value,
            RequestStatus.PENDING.value,
            RequestStatus.UNRELEASED.value,
        }:
            continue
        if not season_episodes:
            return True

        episode_statuses = {
            getattr(episode.status, "value", episode.status) for episode in season_episodes
        }
        if RequestStatus.AVAILABLE.value not in episode_statuses and (
            RequestStatus.PENDING.value in episode_statuses
            or RequestStatus.UNRELEASED.value in episode_statuses
            or season_status == RequestStatus.PARTIALLY_AVAILABLE.value
        ):
            return True
    return False


def _count_request_episode_states(seasons_data: list[dict[str, object]]) -> dict[str, int]:
    """Aggregate TV episode counts across all serialized seasons."""
    return {
        "available": sum(_normalize_int(season.get("available_count")) for season in seasons_data),
        "pending": sum(_normalize_int(season.get("pending_count")) for season in seasons_data),
        "unreleased": sum(
            _normalize_int(season.get("unreleased_count")) for season in seasons_data
        ),
        "total": sum(_normalize_int(season.get("total_count")) for season in seasons_data),
    }


def _compute_sync_metadata(
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
    needs_plex_enrichment = _has_unresolved_partial_tv_data(seasons, episodes_by_season)
    refresh_in_progress = request_id in _DETAILS_SYNC_TASKS
    if (missing or stale or needs_plex_enrichment) and not refresh_in_progress:
        refresh_in_progress = _schedule_background_episode_refresh(background_tasks, request_id)

    return {
        "has_cached_data": bool(seasons),
        "stale": stale,
        "needs_plex_enrichment": needs_plex_enrichment,
        "refresh_in_progress": refresh_in_progress,
        "last_synced_at": newest_synced.isoformat() if newest_synced else None,
    }


def _count_season_episode_states(episodes: list[Any]) -> dict[str, int]:
    """Count TV episode states for UI summaries."""
    counts = {"available": 0, "pending": 0, "unreleased": 0}
    for episode in episodes:
        status = getattr(episode.status, "value", episode.status)
        if status in counts:
            counts[status] += 1
    return counts


async def _load_tv_seasons_with_episodes(
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
    reason: str | None = None,
) -> None:
    """Decline a request in Overseerr and mark it failed locally."""
    effective_settings = await get_effective_settings(db)
    overseerr_service = OverseerrService(settings=effective_settings)
    lifecycle_service = LifecycleService(db)
    queue_service = PendingQueueService(db)

    try:
        if request.overseerr_request_id:
            await overseerr_service.decline_request(request.overseerr_request_id, reason=reason)

        await queue_service.remove_from_queue(request.id)
        await lifecycle_service.mark_as_failed(request.id, reason=reason)
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
            async with _OVERSEERR_SEMAPHORE:
                ov_status = await overseerr_service.get_request_status_cached(
                    req_obj.overseerr_request_id
                )
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
        if req.status == RequestStatus.SEARCHING
        or (
            req.status == RequestStatus.PENDING
            and req.overseerr_request_id
            and (
                overseerr_request_statuses.get(req.id) == "approved"
                or overseerr_media_statuses.get(req.id) == "partially_available"
            )
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

    rejected_result = await db.execute(
        select(RequestModel)
        .where(RequestModel.status == RequestStatus.FAILED)
        .order_by(RequestModel.updated_at.desc())
        .limit(500)
    )
    rejected_requests = list(rejected_result.scalars().all())

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
            "staging_mode_enabled": effective_settings.staging_mode_enabled,
            "pending_requests": pending_requests,
            "pending_items_by_request_id": pending_items_by_request_id,
            "staged_torrents": staged_torrents,
            "staged_request_statuses": staged_request_statuses,
            "replaced_by_titles": replaced_by_titles,
            "completed_requests": completed_requests,
            "rejected_requests": rejected_requests,
            "stats": {
                "active": len(active_requests),
                "pending": len(pending_requests),
                "staged": len(staged_torrents),
                "completed": stats["by_status"].get(RequestStatus.COMPLETED.value, 0),
                "rejected": len(rejected_requests),
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
            await _deny_request_record(request, db, reason="Bulk rejected")

    return RedirectResponse(url=redirect_url, status_code=303)


@router.get("/requests/{request_id}/details")
async def request_details(
    request_id: int,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    from app.siftarr.models.release import Release

    result = await db.execute(select(RequestModel).where(RequestModel.id == request_id))
    request = result.scalar_one_or_none()
    if not request:
        raise HTTPException(status_code=404, detail="Request not found")

    effective_settings = await get_effective_settings(db)
    overseerr_service = OverseerrService(settings=effective_settings)
    details: dict[str, object] = {
        "request": {
            "id": request.id,
            "title": request.title,
            "status": request.status.value,
            "media_type": request.media_type.value,
        }
    }

    try:
        if request.overseerr_request_id:
            ov_task = asyncio.create_task(
                overseerr_service.get_request(request.overseerr_request_id)
            )
            media_details_task = None
            if request.media_type.value == "movie" and request.tmdb_id:
                media_details_task = asyncio.create_task(
                    overseerr_service.get_media_details("movie", request.tmdb_id)
                )
            elif request.media_type.value == "tv" and request.tmdb_id:
                media_details_task = asyncio.create_task(
                    overseerr_service.get_media_details("tv", request.tmdb_id)
                )

            ov = await ov_task
            media: dict[str, object] = {}
            request_status = "unknown"
            if ov:
                media = ov.get("media") or {}
                request_status = overseerr_service.normalize_media_status(media.get("status"))

            media_details = await media_details_task if media_details_task else None

            merged_media = {**media, **(media_details or {})}
            poster = _build_poster_url(
                merged_media.get("posterPath") or merged_media.get("poster"),
            )

            details["overseerr"] = {
                "overview": merged_media.get("overview") or merged_media.get("summary") or "",
                "poster": poster,
                "status": request_status,
                "url": _build_overseerr_media_url(
                    effective_settings.overseerr_url,
                    request.media_type.value,
                    request.tmdb_id,
                ),
            }
    finally:
        await overseerr_service.close()

    release_result = await db.execute(
        select(Release)
        .where(Release.request_id == request_id)
        .order_by(
            Release.score.desc(),
            Release.size.asc(),
            Release.seeders.desc(),
            Release.created_at.desc(),
        )
    )
    releases = list(release_result.scalars().all())
    rules = await db.execute(select(Rule))
    rule_list = list(rules.scalars().all())
    engine = RuleEngine.from_db_rules(rules=rule_list, media_type=request.media_type.value)

    matched = []
    for release in releases:
        evaluation = engine.evaluate(build_prowlarr_release(release))
        coverage = None
        if request.media_type == MediaType.TV:
            coverage = parse_stored_release_coverage(
                release.season_coverage,
                release.season_number,
                release.episode_number,
            )

        payload = _serialize_evaluated_release(release, evaluation, coverage=coverage)
        payload.update(
            {
                "score": release.score,
                "passed": release.passed_rules,
                "downloaded": release.is_downloaded,
                "rejection_reason": evaluation.rejection_reason,
                "season_number": release.season_number,
                "episode_number": release.episode_number,
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
        matched.append(payload)

    matched = _finalize_dashboard_release_payloads(matched)

    details["releases"] = matched

    if request.media_type == MediaType.TV:
        seasons, episodes = await _load_tv_seasons_with_episodes(db, request_id)

        episodes_by_season: dict[int, list[Any]] = {}
        for episode in episodes:
            episodes_by_season.setdefault(episode.season_id, []).append(episode)

        sync_state = _compute_sync_metadata(
            seasons,
            episodes_by_season,
            request_id,
            background_tasks,
        )

        seasons_data = []
        known_season_numbers: list[int] = []
        for season in seasons:
            known_season_numbers.append(season.season_number)
            season_episodes = episodes_by_season.get(season.id, [])
            available_count = sum(
                1 for ep in season_episodes if ep.status == RequestStatus.AVAILABLE
            )
            state_counts = _count_season_episode_states(season_episodes)
            season_data = {
                "id": season.id,
                "season_number": season.season_number,
                "status": season.status.value,
                "available_count": available_count,
                "total_count": len(season_episodes),
                "pending_count": state_counts["pending"],
                "unreleased_count": state_counts["unreleased"],
                "episodes": [
                    {
                        "id": ep.id,
                        "episode_number": ep.episode_number,
                        "title": ep.title,
                        "air_date": ep.air_date.isoformat() if ep.air_date else None,
                        "status": ep.status.value,
                        "release_id": ep.release_id,
                    }
                    for ep in season_episodes
                ],
            }
            seasons_data.append(season_data)

        known_total_seasons = len(known_season_numbers)
        for release in matched:
            if "covered_seasons" not in release and not release.get("is_complete_series"):
                continue

            release["known_total_seasons"] = known_total_seasons
            covered_seasons = _coerce_int_list(release.get("covered_seasons"))
            release["covers_all_known_seasons"] = bool(
                known_total_seasons
                and (
                    release.get("is_complete_series") or len(covered_seasons) >= known_total_seasons
                )
            )
            _apply_release_size_per_season_metadata(release)

        releases_by_season: dict[int, list[dict[str, object]]] = {}
        releases_by_episode: dict[tuple[int, int], list[dict[str, object]]] = {}
        for r in matched:
            sn = r.get("season_number")
            en = r.get("episode_number")
            covered_seasons = _coerce_int_list(r.get("covered_seasons"))
            if r.get("covers_all_known_seasons"):
                covered_seasons = known_season_numbers
            if isinstance(en, int) and isinstance(sn, int):
                key = (sn, en)
                if key not in releases_by_episode:
                    releases_by_episode[key] = []
                releases_by_episode[key].append(r)
            elif covered_seasons:
                for covered_season in covered_seasons:
                    if covered_season not in releases_by_season:
                        releases_by_season[covered_season] = []
                    releases_by_season[covered_season].append(r)
            elif isinstance(sn, int):
                if sn not in releases_by_season:
                    releases_by_season[sn] = []
                releases_by_season[sn].append(r)

        details["tv_info"] = {
            "seasons": seasons_data,
            "releases_by_season": {str(k): v for k, v in releases_by_season.items()},
            "releases_by_episode": {f"{k[0]}-{k[1]}": v for k, v in releases_by_episode.items()},
            "sync_state": sync_state,
            "aggregate_counts": _count_request_episode_states(seasons_data),
        }

    return JSONResponse(details, background=background_tasks)


@router.get("/requests/{request_id}/seasons")
async def get_request_seasons(
    request_id: int,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Get seasons and episodes for a TV request."""
    result = await db.execute(select(RequestModel).where(RequestModel.id == request_id))
    request = result.scalar_one_or_none()
    if not request:
        raise HTTPException(status_code=404, detail="Request not found")

    if request.media_type != MediaType.TV:
        return JSONResponse({"seasons": [], "message": "Request is not a TV show"})

    seasons, episodes = await _load_tv_seasons_with_episodes(db, request_id)
    episodes_by_season: dict[int, list[Any]] = {}
    for episode in episodes:
        episodes_by_season.setdefault(episode.season_id, []).append(episode)
    sync_state = _compute_sync_metadata(
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
            "pending_count": _count_season_episode_states(season_episodes)["pending"],
            "unreleased_count": _count_season_episode_states(season_episodes)["unreleased"],
            "episodes": [
                {
                    "id": ep.id,
                    "episode_number": ep.episode_number,
                    "title": ep.title,
                    "air_date": ep.air_date.isoformat() if ep.air_date else None,
                    "status": ep.status.value,
                    "release_id": ep.release_id,
                }
                for ep in season_episodes
            ],
        }
        seasons_data.append(season_data)

    return JSONResponse(
        {"seasons": seasons_data, "sync_state": sync_state},
        background=background_tasks,
    )


@router.post("/requests/{request_id}/seasons/{season_number}/search")
async def search_season_packs(
    request_id: int,
    season_number: int,
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Search for season packs for a specific season."""
    result = await db.execute(select(RequestModel).where(RequestModel.id == request_id))
    request = result.scalar_one_or_none()
    if not request:
        raise HTTPException(status_code=404, detail="Request not found")

    if request.media_type != MediaType.TV:
        raise HTTPException(status_code=400, detail="Request is not a TV show")

    if not request.tvdb_id:
        raise HTTPException(status_code=400, detail="No TVDB ID available")

    runtime_settings = await get_effective_settings(db)
    prowlarr = ProwlarrService(settings=runtime_settings)

    try:
        search_result = await prowlarr.search_by_tvdbid(
            tvdbid=request.tvdb_id,
            title=request.title,
            season=season_number,
            year=request.year,
        )

        if search_result.error:
            return JSONResponse({"error": search_result.error, "releases": []})

        rules_result = await db.execute(select(Rule))
        rules = list(rules_result.scalars().all())
        engine = RuleEngine.from_db_rules(rules=rules, media_type="tv")

        releases = []
        for release in search_result.releases:
            coverage = parse_release_coverage(release.title)
            if coverage.episode_number is not None:
                continue
            if coverage.is_complete_series:
                continue
            if coverage.season_numbers != (season_number,):
                continue
            evaluation = engine.evaluate(release)
            releases.append(_serialize_evaluated_release(release, evaluation, coverage=coverage))

        return JSONResponse({"releases": _finalize_dashboard_release_payloads(releases)})
    finally:
        pass


@router.post("/requests/{request_id}/seasons/search-all")
async def search_all_season_packs(
    request_id: int,
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Search broadly for TV season packs without downloading anything."""
    from app.siftarr.models.season import Season

    result = await db.execute(select(RequestModel).where(RequestModel.id == request_id))
    request = result.scalar_one_or_none()
    if not request:
        raise HTTPException(status_code=404, detail="Request not found")

    if request.media_type != MediaType.TV:
        raise HTTPException(status_code=400, detail="Request is not a TV show")

    if not request.tvdb_id:
        raise HTTPException(status_code=400, detail="No TVDB ID available")

    seasons_result = await db.execute(
        select(Season).where(Season.request_id == request_id).order_by(Season.season_number)
    )
    seasons = list(seasons_result.scalars().all())
    known_total_seasons = len(seasons) or None

    runtime_settings = await get_effective_settings(db)
    prowlarr = ProwlarrService(settings=runtime_settings)

    try:
        search_result = await prowlarr.search_by_tvdbid(
            tvdbid=request.tvdb_id,
            title=request.title,
            year=request.year,
        )

        if search_result.error:
            return JSONResponse({"error": search_result.error, "releases": []})

        rules_result = await db.execute(select(Rule))
        rules = list(rules_result.scalars().all())
        engine = RuleEngine.from_db_rules(rules=rules, media_type="tv")

        releases = []
        for release in search_result.releases:
            coverage = parse_release_coverage(release.title)
            if coverage.episode_number is not None:
                continue
            if not coverage.is_complete_series and len(coverage.season_numbers) <= 1:
                continue

            evaluation = engine.evaluate(release)
            releases.append(
                _serialize_evaluated_release(
                    release,
                    evaluation,
                    coverage=coverage,
                    known_total_seasons=known_total_seasons,
                )
            )

        return JSONResponse(
            {
                "releases": _finalize_dashboard_release_payloads(releases),
                "known_total_seasons": known_total_seasons,
            }
        )
    finally:
        pass


@router.post("/requests/{request_id}/seasons/{season_number}/episodes/{episode_number}/search")
async def search_episode(
    request_id: int,
    season_number: int,
    episode_number: int,
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Search for a specific episode."""
    result = await db.execute(select(RequestModel).where(RequestModel.id == request_id))
    request = result.scalar_one_or_none()
    if not request:
        raise HTTPException(status_code=404, detail="Request not found")

    if request.media_type != MediaType.TV:
        raise HTTPException(status_code=400, detail="Request is not a TV show")

    if not request.tvdb_id:
        raise HTTPException(status_code=400, detail="No TVDB ID available")

    runtime_settings = await get_effective_settings(db)
    prowlarr = ProwlarrService(settings=runtime_settings)

    try:
        search_result = await prowlarr.search_by_tvdbid(
            tvdbid=request.tvdb_id,
            title=request.title,
            season=season_number,
            episode=episode_number,
            year=request.year,
        )

        if search_result.error:
            return JSONResponse({"error": search_result.error, "releases": []})

        rules_result = await db.execute(select(Rule))
        rules = list(rules_result.scalars().all())
        engine = RuleEngine.from_db_rules(rules=rules, media_type="tv")

        releases = []
        for release in search_result.releases:
            coverage = parse_release_coverage(release.title)
            if coverage.is_complete_series:
                continue
            if coverage.season_numbers != (season_number,):
                continue
            if coverage.episode_number != episode_number:
                continue
            if not _is_exact_single_episode_release(release.title, season_number, episode_number):
                continue
            evaluation = engine.evaluate(release)
            releases.append(_serialize_evaluated_release(release, evaluation))

        return JSONResponse({"releases": _finalize_dashboard_release_payloads(releases)})
    finally:
        pass


@router.post("/requests/{request_id}/releases/{release_id}/use")
async def use_request_release(
    request_id: int,
    release_id: int,
    redirect_to: str | None = Form(default=None),
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    """Stage or send a selected stored release for a request."""
    request = await _load_request_record(db, request_id)

    from app.siftarr.models.release import Release

    release_result = await db.execute(
        select(Release).where(Release.id == release_id, Release.request_id == request_id)
    )
    release = release_result.scalar_one_or_none()
    if not release:
        raise HTTPException(status_code=404, detail="Release not found")

    await use_releases(db, request, [release], selection_source="manual")
    return RedirectResponse(
        url=_selection_redirect_url(redirect_to, request),
        status_code=303,
    )


@router.post("/requests/{request_id}/manual-release/use")
async def use_manual_release(
    request_id: int,
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
) -> RedirectResponse:
    """Persist and use an ad hoc manual-search release for a request."""
    request = await _load_request_record(db, request_id)

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
    return RedirectResponse(
        url=_selection_redirect_url(redirect_to, request),
        status_code=303,
    )


@router.post("/requests/{request_id}/deny")
async def deny_request(
    request_id: int,
    redirect_to: str | None = Form(default=None),
    reason: str | None = Form(default=None),
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    """Decline a request in Overseerr and mark as failed."""
    result = await db.execute(select(RequestModel).where(RequestModel.id == request_id))
    request = result.scalar_one_or_none()

    if not request:
        raise HTTPException(status_code=404, detail="Request not found")

    await _deny_request_record(request, db, reason=reason)
    return RedirectResponse(url=redirect_to or "/", status_code=303)


@router.post("/requests/{request_id}/refresh-plex")
async def refresh_plex(
    request_id: int,
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Force a Plex re-sync for a TV request regardless of staleness."""
    from app.siftarr.services.episode_sync_service import EpisodeSyncService

    result = await db.execute(select(RequestModel).where(RequestModel.id == request_id))
    request = result.scalar_one_or_none()

    if not request:
        raise HTTPException(status_code=404, detail="Request not found")

    if request.media_type != MediaType.TV:
        return JSONResponse({"error": "Request is not a TV show"})

    effective_settings = await get_effective_settings(db)
    plex_service = PlexService(settings=effective_settings)

    try:
        episode_sync = EpisodeSyncService(db, plex=plex_service)
        await episode_sync.sync_episodes(request_id, force_plex_refresh=True)
        return JSONResponse({"status": "success", "message": "Plex sync completed"})
    except Exception:
        logger.exception("Plex refresh failed for request_id=%s", request_id)
        return JSONResponse({"status": "error", "message": "Plex sync failed"}, status_code=500)
    finally:
        await plex_service.close()


@router.post("/requests/{request_id}/mark-available")
async def mark_series_available(
    request_id: int,
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Mark a TV series available in Overseerr and refresh local episode state."""
    request = await _load_request_record(db, request_id)
    if request.media_type != MediaType.TV:
        raise HTTPException(status_code=400, detail="Request is not a TV show")

    effective_settings = await get_effective_settings(db)
    overseerr_service = OverseerrService(settings=effective_settings)
    plex_service = PlexService(settings=effective_settings)
    try:
        media_id = await overseerr_service.resolve_tv_media_id(
            overseerr_request_id=request.overseerr_request_id,
            tmdb_id=request.tmdb_id,
        )
        if media_id is None:
            raise HTTPException(status_code=400, detail="No Overseerr media ID available")

        success = await overseerr_service.mark_series_available(media_id)
        if not success:
            return JSONResponse(
                {"status": "error", "message": "Failed to mark series available in Overseerr"},
                status_code=502,
            )

        clear_status_cache()
        clear_media_details_cache()

        from app.siftarr.services.episode_sync_service import EpisodeSyncService

        episode_sync = EpisodeSyncService(db, plex=plex_service)
        await episode_sync.sync_episodes(request_id, force_plex_refresh=True)
        return JSONResponse({"status": "success", "message": "Series marked available"})
    finally:
        await overseerr_service.close()
        await plex_service.close()


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
