"""Settings page router for viewing and editing application settings."""

import asyncio
import contextlib
import json
import logging
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.siftarr.config import Settings
from app.siftarr.database import async_session_maker, get_db
from app.siftarr.models.request import MediaType, RequestStatus
from app.siftarr.models.request import Request as RequestModel
from app.siftarr.models.settings import Settings as DBSettings
from app.siftarr.services.async_utils import gather_limited
from app.siftarr.services.connection_tester import ConnectionTester, ConnectionTestResult
from app.siftarr.services.overseerr_service import OverseerrService
from app.siftarr.services.pending_queue_service import PendingQueueService
from app.siftarr.services.plex_polling_service import PlexPollingService
from app.siftarr.services.plex_service import PlexService
from app.siftarr.services.release_selection_service import clear_release_search_cache
from app.siftarr.services.rule_service import RuleService
from app.siftarr.services.runtime_settings import get_effective_settings
from app.siftarr.services.unreleased_service import evaluate_imported_request

router = APIRouter(prefix="/settings", tags=["settings"])
templates = Jinja2Templates(directory="app/siftarr/templates")
logger = logging.getLogger(__name__)


@dataclass(slots=True)
class _PreparedOverseerrImport:
    external_id: str
    media_type: MediaType
    tmdb_id: int | None
    tvdb_id: int | None
    title: str
    year: int | None
    requested_seasons: Any
    requested_episodes: Any
    requester_username: str | None
    requester_email: str | None
    overseerr_request_id: int | None
    media_details: dict | None


def _extract_title_and_year_from_media_details(
    media_details: dict | None,
) -> tuple[str, int | None]:
    """Extract title and year from already-fetched Overseerr media details."""
    if not media_details:
        return "", None

    title = media_details.get("title") or media_details.get("name") or ""
    date_str = media_details.get("releaseDate") or media_details.get("firstAirDate") or ""
    year = None
    if date_str and len(date_str) >= 4:
        with contextlib.suppress(ValueError, TypeError):
            year = int(date_str[:4])
    return title, year


async def _prepare_overseerr_import(
    ov_req: dict[str, Any],
    overseerr_service: OverseerrService,
    semaphore: asyncio.Semaphore,
    media_details_tasks: dict[tuple[str, int], asyncio.Task[dict | None]],
    media_details_lock: asyncio.Lock,
) -> _PreparedOverseerrImport | None:
    """Collect per-request network-backed metadata before serial DB writes."""
    media = ov_req.get("media") or {}
    tmdb_id = media.get("tmdbId")
    tvdb_id = media.get("tvdbId")
    overseerr_request_id = ov_req.get("id")

    if tmdb_id is None and tvdb_id is None:
        return None

    external_id = str(tmdb_id) if tmdb_id is not None else str(tvdb_id)
    media_type_str = media.get("mediaType", "")
    media_type = MediaType.MOVIE if media_type_str == "movie" else MediaType.TV
    requested_seasons = media.get("requestedSeasons")
    requested_episodes = media.get("requestedEpisodes")

    requested_by = ov_req.get("requestedBy") or {}
    username = (
        requested_by.get("username")
        or requested_by.get("plexUsername")
        or requested_by.get("displayName")
    )
    email = requested_by.get("email")

    media_details = None
    media_external_id = tmdb_id if tmdb_id is not None else tvdb_id
    if media_external_id is not None:
        media_type_for_api = "movie" if media_type == MediaType.MOVIE else "tv"
        media_details_key = (media_type_for_api, media_external_id)

        async with media_details_lock:
            media_details_task = media_details_tasks.get(media_details_key)
            if media_details_task is None:

                async def fetch_media_details() -> dict | None:
                    async with semaphore:
                        return await overseerr_service.get_media_details(
                            media_type_for_api, media_external_id
                        )

                media_details_task = asyncio.create_task(fetch_media_details())
                media_details_tasks[media_details_key] = media_details_task

        media_details = await media_details_task

    title, year = _extract_title_and_year_from_media_details(media_details)
    return _PreparedOverseerrImport(
        external_id=external_id,
        media_type=media_type,
        tmdb_id=tmdb_id,
        tvdb_id=tvdb_id,
        title=title,
        year=year,
        requested_seasons=requested_seasons,
        requested_episodes=requested_episodes,
        requester_username=username,
        requester_email=email,
        overseerr_request_id=overseerr_request_id,
        media_details=media_details,
    )


async def _rescan_plex_tv_request(
    request_id: int,
    plex: PlexService,
    runtime_settings: Settings,
) -> bool:
    """Resync one TV request on an isolated DB session."""
    from app.siftarr.services.episode_sync_service import EpisodeSyncService
    from app.siftarr.services.overseerr_service import OverseerrService

    async with async_session_maker() as worker_db:
        overseerr = OverseerrService(settings=runtime_settings)
        episode_sync = EpisodeSyncService(worker_db, overseerr=overseerr, plex=plex)
        try:
            await episode_sync.sync_episodes(request_id, force_plex_refresh=True)
        except Exception:
            await worker_db.rollback()
            logger.exception(
                "Plex TV resync failed for request_id=%s during settings rescan",
                request_id,
            )
            return False
        finally:
            await overseerr.close()

    return True


# Pydantic models for connection settings
class ConnectionSettings(BaseModel):
    """Connection settings model."""

    overseerr_url: str | None = None
    overseerr_api_key: str | None = None
    prowlarr_url: str | None = None
    prowlarr_api_key: str | None = None
    qbittorrent_url: str | None = None
    qbittorrent_username: str | None = None
    qbittorrent_password: str | None = None
    tz: str = "UTC"


class ConnectionTestResponse(BaseModel):
    """Response model for connection test."""

    service: str
    success: bool
    message: str
    details: str | None = None


async def _set_db_setting(
    db: AsyncSession, key: str, value: str, description: str | None = None
) -> None:
    """Set a setting value in the database."""
    result = await db.execute(select(DBSettings).where(DBSettings.key == key))
    setting = result.scalar_one_or_none()
    if setting:
        setting.value = value
    else:
        setting = DBSettings(key=key, value=value, description=description)
        db.add(setting)


async def _build_effective_settings(db: AsyncSession) -> dict:
    """Build effective settings, preferring database values over environment variables."""
    effective = await get_effective_settings(db)

    return {
        "overseerr_url": str(effective.overseerr_url or ""),
        "overseerr_api_key": str(effective.overseerr_api_key or ""),
        "prowlarr_url": str(effective.prowlarr_url or ""),
        "prowlarr_api_key": str(effective.prowlarr_api_key or ""),
        "qbittorrent_url": str(effective.qbittorrent_url or ""),
        "qbittorrent_username": effective.qbittorrent_username,
        "qbittorrent_password": effective.qbittorrent_password,
        "plex_url": str(effective.plex_url or ""),
        "plex_token": effective.plex_token or "",
        "tz": effective.tz,
    }


async def _build_effective_settings_obj(db: AsyncSession) -> Settings:
    """Build effective Settings object, preferring database values over environment variables."""
    eff = await _build_effective_settings(db)
    # Create a new Settings object with effective values
    return Settings(
        overseerr_url=eff["overseerr_url"] or None,
        overseerr_api_key=eff["overseerr_api_key"] or None,
        prowlarr_url=eff["prowlarr_url"] or None,
        prowlarr_api_key=eff["prowlarr_api_key"] or None,
        qbittorrent_url=eff["qbittorrent_url"] or None,
        qbittorrent_username=eff["qbittorrent_username"],
        qbittorrent_password=eff["qbittorrent_password"],
        plex_url=eff["plex_url"] or None,
        plex_token=eff["plex_token"] or None,
        tz=eff["tz"],
    )


async def _build_settings_page_context(request: Request, db: AsyncSession) -> dict:
    """Build the shared context required by the settings page."""
    eff_settings = await _build_effective_settings(db)

    result = await db.execute(
        select(DBSettings).where(DBSettings.key == "staging_mode_enabled"),
    )
    staging_setting = result.scalar_one_or_none()
    staging_enabled = staging_setting.value == "true" if staging_setting else True

    queue_service = PendingQueueService(db)
    ready = await queue_service.get_ready_for_retry()
    pending_count = len(ready)

    status_counts = (
        await db.execute(select(RequestModel.status, func.count()).group_by(RequestModel.status))
    ).all()
    stats_by_status = {s.value: c for s, c in status_counts}

    return {
        "request": request,
        "staging_enabled": staging_enabled,
        "pending_count": pending_count,
        "stats": {
            "total_requests": sum(stats_by_status.values()),
            "completed": stats_by_status.get(RequestStatus.COMPLETED.value, 0),
            "pending": stats_by_status.get(RequestStatus.PENDING.value, 0),
            "failed": stats_by_status.get(RequestStatus.FAILED.value, 0),
        },
        "env": eff_settings,
    }


@router.get("")
async def get_settings_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Display settings page."""
    rule_service = RuleService(db)
    await rule_service.ensure_default_rules()
    context = await _build_settings_page_context(request, db)

    return templates.TemplateResponse(
        request,
        "settings.html",
        context,
    )


@router.post("/rescan-plex")
async def rescan_plex(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Re-scan Plex for availability across existing requests."""
    context = await _build_settings_page_context(request, db)

    try:
        runtime_settings = await get_effective_settings(db)
        plex = PlexService(settings=runtime_settings)
        try:
            result = await db.execute(
                select(RequestModel).where(RequestModel.media_type == MediaType.TV)
            )
            tv_requests = [
                req for req in result.scalars().all() if req.status != RequestStatus.COMPLETED
            ]

            configured_concurrency = getattr(runtime_settings, "plex_sync_concurrency", 1)
            sync_concurrency = (
                configured_concurrency
                if isinstance(configured_concurrency, int) and configured_concurrency > 0
                else 1
            )
            resync_results = await gather_limited(
                (tv_request.id for tv_request in tv_requests),
                sync_concurrency,
                lambda request_id: _rescan_plex_tv_request(request_id, plex, runtime_settings),
            )
            tv_resynced = sum(1 for result in resync_results if result)
            tv_failed = len(resync_results) - tv_resynced

            polling_service = PlexPollingService(db, plex)
            completed = await polling_service.poll()
        finally:
            await plex.close()

        context["message"] = (
            "Plex availability re-scan completed. "
            f"Re-synced {tv_resynced} TV request(s), had {tv_failed} failed TV request(s), "
            f"and transitioned {completed} request(s) to completed."
        )
        context["message_type"] = "success"
    except Exception as exc:
        logger.exception("Plex availability re-scan failed")
        context["message"] = f"Plex availability re-scan failed: {exc}"
        context["message_type"] = "error"

    return templates.TemplateResponse(request, "settings.html", context)


@router.post("/connections")
async def save_connections(
    request: Request,
    db: AsyncSession = Depends(get_db),
    overseerr_url: str | None = Form(None),
    overseerr_api_key: str | None = Form(None),
    prowlarr_url: str | None = Form(None),
    prowlarr_api_key: str | None = Form(None),
    qbittorrent_url: str | None = Form(None),
    qbittorrent_username: str | None = Form(None),
    qbittorrent_password: str | None = Form(None),
    plex_url: str | None = Form(None),
    plex_token: str | None = Form(None),
    tz: str | None = Form(None),
) -> RedirectResponse:
    """Save connection settings to database."""
    await _set_db_setting(db, "overseerr_url", overseerr_url or "", "Overseerr URL")
    await _set_db_setting(db, "overseerr_api_key", overseerr_api_key or "", "Overseerr API key")
    await _set_db_setting(db, "prowlarr_url", prowlarr_url or "", "Prowlarr URL")
    await _set_db_setting(db, "prowlarr_api_key", prowlarr_api_key or "", "Prowlarr API key")
    await _set_db_setting(db, "qbittorrent_url", qbittorrent_url or "", "qBittorrent URL")
    await _set_db_setting(
        db, "qbittorrent_username", qbittorrent_username or "", "qBittorrent username"
    )
    await _set_db_setting(
        db, "qbittorrent_password", qbittorrent_password or "", "qBittorrent password"
    )
    await _set_db_setting(db, "plex_url", plex_url or "", "Plex URL")
    await _set_db_setting(db, "plex_token", plex_token or "", "Plex token")
    if tz:
        await _set_db_setting(db, "tz", tz, "Timezone")

    await db.commit()

    return RedirectResponse(url="/settings?saved=true", status_code=303)


@router.post("/connections/reset")
async def reset_connections(
    request: Request,
) -> RedirectResponse:
    """Reset connection settings by clearing database values."""
    # This just redirects - the effective settings will fall back to env vars
    return RedirectResponse(url="/settings?reset=true", status_code=303)


# API endpoints for testing connections
@router.get("/api/connections", response_model=dict)
async def get_connections_api(db: AsyncSession = Depends(get_db)) -> dict:
    """Get current connection settings (for API)."""
    eff = await _build_effective_settings(db)
    return {
        "overseerr_url": eff["overseerr_url"],
        "overseerr_api_key": eff["overseerr_api_key"],
        "prowlarr_url": eff["prowlarr_url"],
        "prowlarr_api_key": eff["prowlarr_api_key"],
        "qbittorrent_url": eff["qbittorrent_url"],
        "qbittorrent_username": eff["qbittorrent_username"],
        "qbittorrent_password": eff["qbittorrent_password"],
        "tz": eff["tz"],
    }


@router.post("/api/test/overseerr", response_model=ConnectionTestResponse)
async def test_overseerr_connection(db: AsyncSession = Depends(get_db)) -> ConnectionTestResponse:
    """Test connection to Overseerr."""
    eff_settings = await _build_effective_settings_obj(db)
    result: ConnectionTestResult = await ConnectionTester.test_overseerr(eff_settings)
    return ConnectionTestResponse(
        service="overseerr",
        success=result.success,
        message=result.message,
        details=result.details,
    )


@router.post("/api/test/prowlarr", response_model=ConnectionTestResponse)
async def test_prowlarr_connection(db: AsyncSession = Depends(get_db)) -> ConnectionTestResponse:
    """Test connection to Prowlarr."""
    eff_settings = await _build_effective_settings_obj(db)
    result: ConnectionTestResult = await ConnectionTester.test_prowlarr(eff_settings)
    return ConnectionTestResponse(
        service="prowlarr",
        success=result.success,
        message=result.message,
        details=result.details,
    )


@router.post("/api/test/qbittorrent", response_model=ConnectionTestResponse)
async def test_qbittorrent_connection(db: AsyncSession = Depends(get_db)) -> ConnectionTestResponse:
    """Test connection to qBittorrent."""
    eff_settings = await _build_effective_settings_obj(db)
    result: ConnectionTestResult = await ConnectionTester.test_qbittorrent(eff_settings)
    return ConnectionTestResponse(
        service="qbittorrent",
        success=result.success,
        message=result.message,
        details=result.details,
    )


@router.post("/api/test/plex", response_model=ConnectionTestResponse)
async def test_plex_connection(db: AsyncSession = Depends(get_db)) -> ConnectionTestResponse:
    """Test connection to Plex."""
    eff_settings = await _build_effective_settings_obj(db)
    result: ConnectionTestResult = await ConnectionTester.test_plex(eff_settings)
    return ConnectionTestResponse(
        service="plex",
        success=result.success,
        message=result.message,
        details=result.details,
    )


@router.post("/api/test/all", response_model=list[ConnectionTestResponse])
async def test_all_connections(db: AsyncSession = Depends(get_db)) -> list[ConnectionTestResponse]:
    """Test connections to all services."""
    eff_settings = await _build_effective_settings_obj(db)

    results = []
    for service_name, tester in [
        ("overseerr", ConnectionTester.test_overseerr),
        ("prowlarr", ConnectionTester.test_prowlarr),
        ("qbittorrent", ConnectionTester.test_qbittorrent),
        ("plex", ConnectionTester.test_plex),
    ]:
        result: ConnectionTestResult = await tester(eff_settings)
        results.append(
            ConnectionTestResponse(
                service=service_name,
                success=result.success,
                message=result.message,
                details=result.details,
            )
        )

    return results


@router.post("/staging")
async def toggle_staging_mode(
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    """Toggle staging mode."""
    result = await db.execute(
        select(DBSettings).where(DBSettings.key == "staging_mode_enabled"),
    )
    staging_setting = result.scalar_one_or_none()

    if staging_setting:
        staging_setting.value = "false" if staging_setting.value == "true" else "true"
    else:
        staging_setting = DBSettings(
            key="staging_mode_enabled",
            value="true",
            description="Enable staging mode to save torrents locally",
        )
        db.add(staging_setting)

    await db.commit()

    return RedirectResponse(url="/settings", status_code=303)


@router.post("/retry-pending")
async def retry_pending(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Manually trigger retry of pending items."""
    from app.siftarr.main import scheduler_service

    if scheduler_service:
        count = await scheduler_service.trigger_retry_now()
        message = f"Retrying {count} pending items"
    else:
        message = "Scheduler not available"

    return templates.TemplateResponse(
        request,
        "settings.html",
        {
            "request": request,
            "message": message,
            "message_type": "success",
        },
    )


async def _sync_overseerr_generator():
    """Async generator that yields SSE events for Overseerr sync progress."""

    def _sse(data: dict) -> str:
        return f"data: {json.dumps(data)}\n\n"

    try:
        yield _sse({"phase": "connecting"})

        async with async_session_maker() as db:
            eff_settings = await _build_effective_settings(db)

            if not eff_settings.get("overseerr_url") or not eff_settings.get("overseerr_api_key"):
                yield _sse(
                    {
                        "phase": "error",
                        "message": "Overseerr is not configured. Please set URL and API key.",
                    }
                )
                return

            runtime_settings = await get_effective_settings(db)
            overseerr_service = OverseerrService(settings=runtime_settings)
            try:
                yield _sse({"phase": "fetching", "message": "Fetching requests from Overseerr..."})
                overseerr_requests = await overseerr_service.get_all_requests(status=None)

                if not overseerr_requests:
                    yield _sse(
                        {
                            "phase": "complete",
                            "synced": 0,
                            "skipped": 0,
                            "message": "No requests found in Overseerr",
                        }
                    )
                    return

                result = await db.execute(
                    select(RequestModel.external_id, RequestModel.overseerr_request_id)
                )
                existing_rows = result.fetchall()
                existing_external_ids = {row[0] for row in existing_rows}
                existing_request_ids = {row[1] for row in existing_rows if row[1] is not None}

                actionable_requests = []
                for ov_req in overseerr_requests:
                    media = ov_req.get("media") or {}
                    request_status = overseerr_service.normalize_request_status(
                        ov_req.get("status")
                    )
                    media_status = overseerr_service.normalize_media_status(media.get("status"))
                    if request_status not in {"pending", "approved"}:
                        continue
                    if media_status == "available":
                        continue
                    actionable_requests.append(ov_req)

                total = len(actionable_requests)
                yield _sse(
                    {
                        "phase": "fetching",
                        "message": f"Found {total} actionable request(s). Fetching details...",
                    }
                )

                sync_concurrency = max(1, runtime_settings.overseerr_sync_concurrency)
                sync_semaphore = asyncio.Semaphore(sync_concurrency)
                media_details_tasks: dict[tuple[str, int], asyncio.Task[dict | None]] = {}
                media_details_lock = asyncio.Lock()

                prepared_requests = await asyncio.gather(
                    *(
                        _prepare_overseerr_import(
                            ov_req,
                            overseerr_service,
                            sync_semaphore,
                            media_details_tasks,
                            media_details_lock,
                        )
                        for ov_req in actionable_requests
                    ),
                    return_exceptions=True,
                )

                synced_count = 0
                skipped_count = 0
                new_tv_requests: list[RequestModel] = []

                for i, prepared_request in enumerate(prepared_requests):
                    try:
                        if isinstance(prepared_request, BaseException):
                            logger.exception(
                                "Overseerr request prefetch failed during sync",
                                exc_info=prepared_request,
                            )
                            skipped_count += 1
                            yield _sse(
                                {
                                    "phase": "processing",
                                    "current": i + 1,
                                    "total": total,
                                    "title": "(prefetch error)",
                                }
                            )
                            continue

                        if prepared_request is None:
                            skipped_count += 1
                            yield _sse(
                                {
                                    "phase": "processing",
                                    "current": i + 1,
                                    "total": total,
                                    "title": "(skipped)",
                                }
                            )
                            continue

                        prepared = prepared_request

                        yield _sse(
                            {
                                "phase": "processing",
                                "current": i + 1,
                                "total": total,
                                "title": prepared.title or prepared.external_id,
                            }
                        )

                        if (
                            prepared.external_id in existing_external_ids
                            or prepared.overseerr_request_id in existing_request_ids
                        ):
                            skipped_count += 1
                            continue

                        new_request = RequestModel(
                            external_id=prepared.external_id,
                            media_type=prepared.media_type,
                            tmdb_id=prepared.tmdb_id,
                            tvdb_id=prepared.tvdb_id,
                            title=prepared.title,
                            year=prepared.year,
                            requested_seasons=str(prepared.requested_seasons)
                            if prepared.requested_seasons
                            else None,
                            requested_episodes=str(prepared.requested_episodes)
                            if prepared.requested_episodes
                            else None,
                            requester_username=prepared.requester_username,
                            requester_email=prepared.requester_email,
                            status=RequestStatus.PENDING,
                            overseerr_request_id=prepared.overseerr_request_id,
                        )
                        db.add(new_request)
                        await db.flush()
                        await evaluate_imported_request(
                            db,
                            overseerr_service,
                            new_request,
                            logger=logger,
                            prefetched_media_details=prepared.media_details,
                            local_episodes=(),
                        )
                        if prepared.media_type == MediaType.TV:
                            new_tv_requests.append(new_request)
                        existing_external_ids.add(prepared.external_id)
                        if prepared.overseerr_request_id is not None:
                            existing_request_ids.add(prepared.overseerr_request_id)
                        synced_count += 1
                    except Exception:
                        logger.exception("Overseerr request import failed during sync")
                        skipped_count += 1
                        continue

                await db.commit()

                if synced_count > 0:
                    from app.siftarr.services.episode_sync_service import EpisodeSyncService

                    plex_service = PlexService(settings=runtime_settings)
                    try:
                        episode_sync = EpisodeSyncService(
                            db,
                            overseerr=overseerr_service,
                            plex=plex_service,
                        )
                        for req in new_tv_requests:
                            try:
                                await episode_sync.sync_episodes(req.id)
                            except Exception:
                                logger.exception(
                                    "Episode sync failed for request_id=%s during import",
                                    req.id,
                                )
                    finally:
                        await plex_service.close()

                if synced_count > 0:
                    message = f"Synced {synced_count} new request(s) from Overseerr"
                else:
                    message = f"No new actionable requests to sync ({skipped_count} already existed or were already available)"

                yield _sse(
                    {
                        "phase": "complete",
                        "synced": synced_count,
                        "skipped": skipped_count,
                        "message": message,
                    }
                )
            finally:
                await overseerr_service.close()

    except Exception as e:
        logger.exception("Overseerr SSE sync failed")
        yield _sse({"phase": "error", "message": f"Sync error: {e}"})


async def _rescan_plex_generator():
    """Async generator that yields SSE events for Plex re-scan progress."""

    def _sse(data: dict) -> str:
        return f"data: {json.dumps(data)}\n\n"

    try:
        yield _sse({"phase": "connecting"})

        async with async_session_maker() as db:
            runtime_settings = await get_effective_settings(db)
            plex = PlexService(settings=runtime_settings)
            try:
                yield _sse({"phase": "fetching", "message": "Fetching TV requests..."})

                result = await db.execute(
                    select(RequestModel).where(RequestModel.media_type == MediaType.TV)
                )
                tv_requests = [
                    req for req in result.scalars().all() if req.status != RequestStatus.COMPLETED
                ]
                total = len(tv_requests)

                if total == 0:
                    yield _sse(
                        {
                            "phase": "complete",
                            "resynced": 0,
                            "failed": 0,
                            "completed": 0,
                            "message": "No TV requests to re-scan.",
                        }
                    )
                    return

                resynced = 0
                failed = 0

                for i, tv_request in enumerate(tv_requests):
                    yield _sse(
                        {
                            "phase": "processing",
                            "current": i + 1,
                            "total": total,
                            "title": tv_request.title or f"Request #{tv_request.id}",
                        }
                    )
                    success = await _rescan_plex_tv_request(tv_request.id, plex, runtime_settings)
                    if success:
                        resynced += 1
                    else:
                        failed += 1

                yield _sse({"phase": "polling", "message": "Running Plex availability poll..."})

                polling_service = PlexPollingService(db, plex)
                completed = await polling_service.poll()

                message = (
                    f"Plex re-scan completed. "
                    f"Re-synced {resynced} TV request(s), "
                    f"{failed} failed, "
                    f"{completed} transitioned to completed."
                )
                yield _sse(
                    {
                        "phase": "complete",
                        "resynced": resynced,
                        "failed": failed,
                        "completed": completed,
                        "message": message,
                    }
                )
            finally:
                await plex.close()

    except Exception as e:
        logger.exception("Plex SSE re-scan failed")
        yield _sse({"phase": "error", "message": f"Plex re-scan error: {e}"})


@router.get("/api/rescan-plex/stream")
async def rescan_plex_stream() -> StreamingResponse:
    """Stream Plex re-scan progress via SSE."""
    return StreamingResponse(
        _rescan_plex_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/api/sync-overseerr/stream")
async def sync_overseerr_stream() -> StreamingResponse:
    """Stream Overseerr sync progress via SSE."""
    return StreamingResponse(
        _sync_overseerr_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/sync-overseerr")
async def sync_overseerr(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Sync with Overseerr for new requests."""
    context = await _build_settings_page_context(request, db)
    eff_settings = context["env"]

    # Sync logic
    message = "Overseerr sync completed"
    message_type = "success"
    synced_count = 0
    skipped_count = 0

    # Check if Overseerr is configured
    if not eff_settings.get("overseerr_url") or not eff_settings.get("overseerr_api_key"):
        message = "Overseerr is not configured. Please set URL and API key."
        message_type = "error"
    else:
        runtime_settings = await get_effective_settings(db)
        overseerr_service = OverseerrService(settings=runtime_settings)
        try:
            overseerr_requests = await overseerr_service.get_all_requests(status=None)

            if not overseerr_requests:
                message = "No requests found in Overseerr"
                message_type = "success"
            else:
                result = await db.execute(
                    select(RequestModel.external_id, RequestModel.overseerr_request_id)
                )
                existing_rows = result.fetchall()
                existing_external_ids = {row[0] for row in existing_rows}
                existing_request_ids = {row[1] for row in existing_rows if row[1] is not None}

                actionable_requests = []
                for ov_req in overseerr_requests:
                    media = ov_req.get("media") or {}
                    request_status = overseerr_service.normalize_request_status(
                        ov_req.get("status")
                    )
                    media_status = overseerr_service.normalize_media_status(media.get("status"))

                    if request_status not in {"pending", "approved"}:
                        continue
                    if media_status == "available":
                        continue

                    actionable_requests.append(ov_req)

                sync_concurrency = max(1, runtime_settings.overseerr_sync_concurrency)
                sync_semaphore = asyncio.Semaphore(sync_concurrency)
                media_details_tasks: dict[tuple[str, int], asyncio.Task[dict | None]] = {}
                media_details_lock = asyncio.Lock()

                prepared_requests = await asyncio.gather(
                    *(
                        _prepare_overseerr_import(
                            ov_req,
                            overseerr_service,
                            sync_semaphore,
                            media_details_tasks,
                            media_details_lock,
                        )
                        for ov_req in actionable_requests
                    ),
                    return_exceptions=True,
                )

                # Process each request
                new_tv_requests: list[RequestModel] = []
                for prepared_request in prepared_requests:
                    try:
                        if isinstance(prepared_request, BaseException):
                            logger.exception(
                                "Overseerr request prefetch failed during sync",
                                exc_info=prepared_request,
                            )
                            skipped_count += 1
                            continue

                        if prepared_request is None:
                            skipped_count += 1
                            continue

                        prepared = prepared_request

                        # Skip if already exists
                        if (
                            prepared.external_id in existing_external_ids
                            or prepared.overseerr_request_id in existing_request_ids
                        ):
                            skipped_count += 1
                            continue

                        # Create new request
                        new_request = RequestModel(
                            external_id=prepared.external_id,
                            media_type=prepared.media_type,
                            tmdb_id=prepared.tmdb_id,
                            tvdb_id=prepared.tvdb_id,
                            title=prepared.title,
                            year=prepared.year,
                            requested_seasons=str(prepared.requested_seasons)
                            if prepared.requested_seasons
                            else None,
                            requested_episodes=str(prepared.requested_episodes)
                            if prepared.requested_episodes
                            else None,
                            requester_username=prepared.requester_username,
                            requester_email=prepared.requester_email,
                            status=RequestStatus.PENDING,
                            overseerr_request_id=prepared.overseerr_request_id,
                        )
                        db.add(new_request)
                        await db.flush()
                        await evaluate_imported_request(
                            db,
                            overseerr_service,
                            new_request,
                            logger=logger,
                            prefetched_media_details=prepared.media_details,
                            local_episodes=(),
                        )
                        if prepared.media_type == MediaType.TV:
                            new_tv_requests.append(new_request)
                        existing_external_ids.add(
                            prepared.external_id
                        )  # Prevent duplicates in same sync
                        if prepared.overseerr_request_id is not None:
                            existing_request_ids.add(prepared.overseerr_request_id)
                        synced_count += 1
                    except Exception:
                        # Log individual request processing errors but continue
                        logger.exception("Overseerr request import failed during sync")
                        skipped_count += 1
                        continue

                await db.commit()

                if synced_count > 0:
                    from app.siftarr.services.episode_sync_service import EpisodeSyncService

                    plex_service = PlexService(settings=runtime_settings)
                    try:
                        episode_sync = EpisodeSyncService(
                            db,
                            overseerr=overseerr_service,
                            plex=plex_service,
                        )
                        for req in new_tv_requests:
                            try:
                                await episode_sync.sync_episodes(req.id)
                            except Exception:
                                logger.exception(
                                    "Episode sync failed for request_id=%s during import", req.id
                                )
                    finally:
                        await plex_service.close()

                    message = f"Synced {synced_count} new request(s) from Overseerr"
                    message_type = "success"
                else:
                    message = f"No new actionable requests to sync ({skipped_count} already existed or were already available)"
                    message_type = "success"
        except Exception as e:
            message = f"Sync error: {str(e)}"
            message_type = "error"
        finally:
            await overseerr_service.close()

    context["message"] = message
    context["message_type"] = message_type

    return templates.TemplateResponse(
        request,
        "settings.html",
        context,
    )


@router.post("/clear-cache")
async def clear_cache(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Clear app-side persisted release results and Overseerr status cache."""
    context = await _build_settings_page_context(request, db)

    try:
        release_result = await clear_release_search_cache(db)
        context["message"] = (
            "Cleared app search cache: "
            f"removed {release_result['deleted_releases']} stored release result(s) and "
            f"detached {release_result['detached_episode_refs']} episode link(s)."
        )
        context["message_type"] = "success"
    except Exception as exc:
        logger.exception("Failed to clear app search cache")
        await db.rollback()
        context["message"] = f"Failed to clear app search cache: {exc}"
        context["message_type"] = "error"

    return templates.TemplateResponse(request, "settings.html", context)


@router.post("/reseed-rules")
async def reseed_rules(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Reseed default rules."""
    rule_service = RuleService(db)
    await rule_service.seed_default_rules()

    return templates.TemplateResponse(
        request,
        "settings.html",
        {
            "request": request,
            "message": "Default rules have been seeded",
            "message_type": "success",
        },
    )
