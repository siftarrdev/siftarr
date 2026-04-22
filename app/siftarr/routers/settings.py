"""Consolidated settings router."""

import logging
import os
from collections.abc import AsyncGenerator
from typing import Any

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.siftarr.config import get_settings
from app.siftarr.database import async_session_maker, get_db
from app.siftarr.models.request import Request as RequestModel
from app.siftarr.models.request import RequestStatus
from app.siftarr.services.connection_tester import ConnectionTester, ConnectionTestResult
from app.siftarr.services.overseerr_service import OverseerrService
from app.siftarr.services.plex_polling_service import PlexPollingService
from app.siftarr.services.plex_service import PlexService
from app.siftarr.services.release_storage import clear_release_search_cache
from app.siftarr.services.rule_service import RuleService
from app.siftarr.services.scheduler_service import (
    PLEX_POLL_JOB_NAME,
    PLEX_RECENT_SCAN_JOB_NAME,
)
from app.siftarr.services.settings_service import (
    build_effective_settings,
    build_effective_settings_obj,
    build_manual_plex_job_message,
    build_plex_job_statuses,
    build_settings_page_context,
    build_sse_progress,
    import_overseerr_requests,
    prepare_overseerr_import,
    rescan_plex_generator,
    rescan_plex_requests,
    rescan_plex_tv_request,
    run_bounded_with_progress,
    sync_overseerr_generator,
)
from app.siftarr.services.unreleased_service import evaluate_imported_request

router = APIRouter(prefix="/settings", tags=["settings"])
templates = Jinja2Templates(directory="app/siftarr/templates")
logger = logging.getLogger(__name__)


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


_RUNTIME_SETTINGS_ENV_KEYS = {
    "overseerr_url": "OVERSEERR_URL",
    "overseerr_api_key": "OVERSEERR_API_KEY",
    "prowlarr_url": "PROWLARR_URL",
    "prowlarr_api_key": "PROWLARR_API_KEY",
    "qbittorrent_url": "QBITTORRENT_URL",
    "qbittorrent_username": "QBITTORRENT_USERNAME",
    "qbittorrent_password": "QBITTORRENT_PASSWORD",
    "plex_url": "PLEX_URL",
    "plex_token": "PLEX_TOKEN",
    "tz": "TZ",
    "staging_mode_enabled": "STAGING_MODE_ENABLED",
}


async def _set_db_setting(db, key: str, value: str, description: str | None = None) -> None:
    del db, description
    env_name = _RUNTIME_SETTINGS_ENV_KEYS.get(key, key.upper())
    os.environ[env_name] = value
    get_settings.cache_clear()


def _clear_runtime_setting(*keys: str) -> None:
    for key in keys:
        env_name = _RUNTIME_SETTINGS_ENV_KEYS.get(key, key.upper())
        os.environ.pop(env_name, None)
    get_settings.cache_clear()


async def _build_plex_job_statuses(db) -> list[dict[str, Any]]:
    return await build_plex_job_statuses(
        db,
        recent_scan_job_name=PLEX_RECENT_SCAN_JOB_NAME,
        poll_job_name=PLEX_POLL_JOB_NAME,
    )


async def _build_effective_settings(db) -> dict[str, Any]:
    del db
    return await build_effective_settings()


async def _build_effective_settings_obj(db):
    return await build_effective_settings_obj(db)


async def _build_settings_page_context(request, db) -> dict[str, Any]:
    return await build_settings_page_context(
        request,
        db,
        request_model=RequestModel,
        request_status_enum=RequestStatus,
        build_plex_job_statuses_func=_build_plex_job_statuses,
    )


async def _prepare_overseerr_import(
    ov_req: dict[str, Any],
    overseerr_service,
    semaphore,
    media_details_tasks,
    media_details_lock,
):
    return await prepare_overseerr_import(
        ov_req,
        overseerr_service,
        semaphore,
        media_details_tasks,
        media_details_lock,
    )


async def _import_overseerr_requests(db, runtime_settings) -> tuple[int, int]:
    return await import_overseerr_requests(
        db,
        runtime_settings,
        overseerr_service_cls=OverseerrService,
        plex_service_cls=PlexService,
        evaluate_imported_request_func=evaluate_imported_request,
        prepare_overseerr_import_func=_prepare_overseerr_import,
        logger=logger,
    )


async def _rescan_plex_tv_request(
    request_id: int,
    plex,
    runtime_settings,
) -> bool:
    return await rescan_plex_tv_request(
        request_id,
        plex,
        runtime_settings,
        session_maker=async_session_maker,
        logger=logger,
    )


async def _run_bounded_with_progress(
    items: list[Any],
    limit: int,
    worker,
    *,
    on_event,
    phase: str,
) -> list[Any]:
    return await run_bounded_with_progress(
        items,
        limit,
        worker,
        on_event=on_event,
        phase=phase,
        build_sse_progress_func=build_sse_progress,
    )


async def _rescan_plex_requests(
    db,
    runtime_settings,
    plex,
    *,
    on_event=None,
    shallow: bool = False,
) -> tuple[int, int, int]:
    return await rescan_plex_requests(
        db,
        runtime_settings,
        plex,
        on_event=on_event,
        shallow=shallow,
        plex_polling_service_cls=PlexPollingService,
        build_sse_progress_func=build_sse_progress,
        run_bounded_with_progress_func=_run_bounded_with_progress,
        rescan_plex_tv_request_func=_rescan_plex_tv_request,
    )


async def _sync_overseerr_generator() -> AsyncGenerator[str, None]:
    async for event in sync_overseerr_generator(
        async_session_maker=async_session_maker,
        build_effective_settings_func=_build_effective_settings,
        import_overseerr_requests_func=_import_overseerr_requests,
        build_sse_progress_func=build_sse_progress,
        logger=logger,
    ):
        yield event


async def _rescan_plex_generator(shallow: bool = False) -> AsyncGenerator[str, None]:
    async for event in rescan_plex_generator(
        shallow=shallow,
        async_session_maker=async_session_maker,
        plex_service_cls=PlexService,
        rescan_plex_requests_func=_rescan_plex_requests,
        build_sse_progress_func=build_sse_progress,
        logger=logger,
    ):
        yield event


@router.get("")
async def get_settings_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Display settings page."""
    rule_service = RuleService(db)
    await rule_service.ensure_default_rules()
    context = await _build_settings_page_context(request, db)
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
    """Save connection settings as runtime environment overrides."""
    del request
    await _set_db_setting(db, "overseerr_url", overseerr_url or "", "Overseerr URL")
    await _set_db_setting(db, "overseerr_api_key", overseerr_api_key or "", "Overseerr API key")
    await _set_db_setting(db, "prowlarr_url", prowlarr_url or "", "Prowlarr URL")
    await _set_db_setting(db, "prowlarr_api_key", prowlarr_api_key or "", "Prowlarr API key")
    await _set_db_setting(db, "qbittorrent_url", qbittorrent_url or "", "qBittorrent URL")
    await _set_db_setting(
        db,
        "qbittorrent_username",
        qbittorrent_username or "",
        "qBittorrent username",
    )
    await _set_db_setting(
        db,
        "qbittorrent_password",
        qbittorrent_password or "",
        "qBittorrent password",
    )
    await _set_db_setting(db, "plex_url", plex_url or "", "Plex URL")
    await _set_db_setting(db, "plex_token", plex_token or "", "Plex token")
    if tz:
        await _set_db_setting(db, "tz", tz, "Timezone")
    await db.commit()
    return RedirectResponse(url="/settings?saved=true", status_code=303)


@router.post("/connections/reset")
async def reset_connections(request: Request) -> RedirectResponse:
    """Reset connection settings by clearing runtime environment overrides."""
    del request
    _clear_runtime_setting(
        "overseerr_url",
        "overseerr_api_key",
        "prowlarr_url",
        "prowlarr_api_key",
        "qbittorrent_url",
        "qbittorrent_username",
        "qbittorrent_password",
        "plex_url",
        "plex_token",
        "tz",
    )
    return RedirectResponse(url="/settings?reset=true", status_code=303)


@router.get("/api/connections", response_model=dict)
async def get_connections_api(db: AsyncSession = Depends(get_db)) -> dict:
    """Get current connection settings (for API)."""
    effective = await _build_effective_settings(db)
    return {
        "overseerr_url": effective["overseerr_url"],
        "overseerr_api_key": effective["overseerr_api_key"],
        "prowlarr_url": effective["prowlarr_url"],
        "prowlarr_api_key": effective["prowlarr_api_key"],
        "qbittorrent_url": effective["qbittorrent_url"],
        "qbittorrent_username": effective["qbittorrent_username"],
        "qbittorrent_password": effective["qbittorrent_password"],
        "tz": effective["tz"],
    }


@router.post("/api/test/overseerr", response_model=ConnectionTestResponse)
async def test_overseerr_connection(db: AsyncSession = Depends(get_db)) -> ConnectionTestResponse:
    """Test connection to Overseerr."""
    effective_settings = await _build_effective_settings_obj(db)
    result: ConnectionTestResult = await ConnectionTester.test_overseerr(effective_settings)
    return ConnectionTestResponse(
        service="overseerr",
        success=result.success,
        message=result.message,
        details=result.details,
    )


@router.post("/api/test/prowlarr", response_model=ConnectionTestResponse)
async def test_prowlarr_connection(db: AsyncSession = Depends(get_db)) -> ConnectionTestResponse:
    """Test connection to Prowlarr."""
    effective_settings = await _build_effective_settings_obj(db)
    result: ConnectionTestResult = await ConnectionTester.test_prowlarr(effective_settings)
    return ConnectionTestResponse(
        service="prowlarr",
        success=result.success,
        message=result.message,
        details=result.details,
    )


@router.post("/api/test/qbittorrent", response_model=ConnectionTestResponse)
async def test_qbittorrent_connection(db: AsyncSession = Depends(get_db)) -> ConnectionTestResponse:
    """Test connection to qBittorrent."""
    effective_settings = await _build_effective_settings_obj(db)
    result: ConnectionTestResult = await ConnectionTester.test_qbittorrent(effective_settings)
    return ConnectionTestResponse(
        service="qbittorrent",
        success=result.success,
        message=result.message,
        details=result.details,
    )


@router.post("/api/test/plex", response_model=ConnectionTestResponse)
async def test_plex_connection(db: AsyncSession = Depends(get_db)) -> ConnectionTestResponse:
    """Test connection to Plex."""
    effective_settings = await _build_effective_settings_obj(db)
    result: ConnectionTestResult = await ConnectionTester.test_plex(effective_settings)
    return ConnectionTestResponse(
        service="plex",
        success=result.success,
        message=result.message,
        details=result.details,
    )


@router.post("/api/test/all", response_model=list[ConnectionTestResponse])
async def test_all_connections(db: AsyncSession = Depends(get_db)) -> list[ConnectionTestResponse]:
    """Test connections to all services."""
    effective_settings = await _build_effective_settings_obj(db)
    results = []
    for service_name, tester in [
        ("overseerr", ConnectionTester.test_overseerr),
        ("prowlarr", ConnectionTester.test_prowlarr),
        ("qbittorrent", ConnectionTester.test_qbittorrent),
        ("plex", ConnectionTester.test_plex),
    ]:
        result: ConnectionTestResult = await tester(effective_settings)
        results.append(
            ConnectionTestResponse(
                service=service_name,
                success=result.success,
                message=result.message,
                details=result.details,
            )
        )
    return results


@router.post("/rescan-plex")
async def rescan_plex(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Run the manual Plex rescan path for existing requests."""
    context = await _build_settings_page_context(request, db)
    try:
        runtime_settings = get_settings()
        plex = PlexService(settings=runtime_settings)
        try:
            tv_resynced, tv_failed, completed = await _rescan_plex_requests(
                db,
                runtime_settings,
                plex,
            )
        finally:
            await plex.close()

        context["message"] = (
            "Manual Plex rescan completed. "
            f"Re-synced {tv_resynced} TV request(s), had {tv_failed} failed TV request(s), "
            f"and transitioned {completed} request(s) to completed."
        )
        context["message_type"] = "success"
    except Exception as exc:
        logger.exception("Plex availability re-scan failed")
        context["message"] = f"Plex availability re-scan failed: {exc}"
        context["message_type"] = "error"
    return templates.TemplateResponse(request, "settings.html", context)


@router.post("/staging")
async def toggle_staging_mode(db: AsyncSession = Depends(get_db)) -> RedirectResponse:
    """Toggle staging mode."""
    del db
    staging_enabled = get_settings().staging_mode_enabled
    await _set_db_setting(
        None,
        "staging_mode_enabled",
        "false" if staging_enabled else "true",
    )
    return RedirectResponse(url="/settings", status_code=303)


@router.post("/retry-pending")
async def retry_pending(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Manually trigger retry of pending items."""
    from app.siftarr.main import scheduler_service

    context = await _build_settings_page_context(request, db)
    if scheduler_service:
        count = await scheduler_service.trigger_retry_now()
        context["message"] = f"Retrying {count} pending items"
        context["message_type"] = "success"
    else:
        context["message"] = "Scheduler not available"
        context["message_type"] = "error"
    return templates.TemplateResponse(request, "settings.html", context)


@router.post("/run-recent-plex-scan")
async def run_recent_plex_scan(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Manually trigger the recent Plex scan scheduler job."""
    from app.siftarr.main import scheduler_service

    context = await _build_settings_page_context(request, db)
    if scheduler_service is None:
        context["message"] = "Scheduler not available"
        context["message_type"] = "error"
        return templates.TemplateResponse(request, "settings.html", context)

    result = await scheduler_service.trigger_recent_plex_scan_now()
    context["message"], context["message_type"] = build_manual_plex_job_message(
        "Recent Plex scan",
        result,
    )
    context["plex_jobs"] = await _build_plex_job_statuses(db)
    return templates.TemplateResponse(request, "settings.html", context)


@router.post("/run-plex-poll")
async def run_plex_poll(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Manually trigger the Plex poll scheduler job."""
    from app.siftarr.main import scheduler_service

    context = await _build_settings_page_context(request, db)
    if scheduler_service is None:
        context["message"] = "Scheduler not available"
        context["message_type"] = "error"
        return templates.TemplateResponse(request, "settings.html", context)

    result = await scheduler_service.trigger_plex_poll_now()
    context["message"], context["message_type"] = build_manual_plex_job_message(
        "Plex poll",
        result,
    )
    context["plex_jobs"] = await _build_plex_job_statuses(db)
    return templates.TemplateResponse(request, "settings.html", context)


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
            f"removed {release_result['deleted_releases']} stored release result(s)."
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
    context = await _build_settings_page_context(request, db)
    context["message"] = "Default rules have been seeded"
    context["message_type"] = "success"
    return templates.TemplateResponse(request, "settings.html", context)


@router.get("/api/rescan-plex/stream")
async def rescan_plex_stream(shallow: bool = False) -> StreamingResponse:
    """Stream Plex re-scan progress via SSE."""
    return StreamingResponse(
        _rescan_plex_generator(shallow=shallow),
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
    effective_settings = context["env"]

    if not effective_settings.get("overseerr_url") or not effective_settings.get(
        "overseerr_api_key"
    ):
        context["message"] = "Overseerr is not configured. Please set URL and API key."
        context["message_type"] = "error"
        return templates.TemplateResponse(request, "settings.html", context)

    try:
        runtime_settings = get_settings()
        synced_count, skipped_count = await _import_overseerr_requests(
            db,
            runtime_settings,
        )
        if synced_count > 0:
            context["message"] = f"Synced {synced_count} new request(s) from Overseerr"
        elif synced_count == 0 and skipped_count == 0:
            context["message"] = "No requests found in Overseerr"
        else:
            context["message"] = (
                "No new actionable requests to sync "
                f"({skipped_count} already existed or were already available)"
            )
        context["message_type"] = "success"
    except Exception as exc:
        context["message"] = f"Sync error: {exc}"
        context["message_type"] = "error"

    return templates.TemplateResponse(request, "settings.html", context)
