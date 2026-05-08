"""Consolidated settings router."""

import logging
import os
from collections.abc import AsyncGenerator
from typing import Any

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.siftarr import database as db_mod
from app.siftarr.config import get_settings, reload_settings
from app.siftarr.database import get_db
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
    ENV_KEY_MAP,
    SettingsStore,
    build_effective_settings,
    build_manual_plex_job_message,
    build_sse_progress,
    prepare_overseerr_import,
)
from app.siftarr.services.settings_service import (
    build_plex_job_statuses as build_plex_job_statuses_svc,
)
from app.siftarr.services.settings_service import (
    build_settings_page_context as build_settings_page_context_svc,
)
from app.siftarr.services.settings_service import (
    import_overseerr_requests as import_overseerr_requests_svc,
)
from app.siftarr.services.settings_service import (
    rescan_plex_generator as rescan_plex_generator_svc,
)
from app.siftarr.services.settings_service import (
    rescan_plex_requests as rescan_plex_requests_svc,
)
from app.siftarr.services.settings_service import (
    rescan_plex_tv_request as rescan_plex_tv_request_svc,
)
from app.siftarr.services.settings_service import (
    run_bounded_with_progress as run_bounded_with_progress_svc,
)
from app.siftarr.services.settings_service import (
    sync_overseerr_generator as sync_overseerr_generator_svc,
)
from app.siftarr.services.unreleased_service import evaluate_imported_request

router = APIRouter(prefix="/settings", tags=["settings"])
templates = Jinja2Templates(directory="app/siftarr/templates")
logger = logging.getLogger(__name__)


def _get_scheduler_service():
    """Late-binding accessor to avoid circular import with main.py."""
    from app.siftarr.main import scheduler_service

    return scheduler_service


class ConnectionSettings(BaseModel):
    """Connection settings model."""

    overseerr_url: str | None = None
    overseerr_api_key: str | None = None
    prowlarr_url: str | None = None
    prowlarr_api_key: str | None = None
    qbittorrent_url: str | None = None
    qbittorrent_api_key: str | None = None
    tz: str = "UTC"


class ConnectionTestResponse(BaseModel):
    """Response model for connection test."""

    service: str
    success: bool
    message: str
    details: str | None = None


# ── Convenience helpers (not pass-through; provide router-level defaults) ──


async def _build_plex_job_statuses(db) -> list[dict[str, Any]]:
    """Build Plex job statuses with router-scoped job name constants."""
    return await build_plex_job_statuses_svc(
        db,
        recent_scan_job_name=PLEX_RECENT_SCAN_JOB_NAME,
        poll_job_name=PLEX_POLL_JOB_NAME,
    )


async def _build_settings_page_context(
    request: Request,
    db: AsyncSession,
    *,
    store: SettingsStore | None = None,
) -> dict[str, Any]:
    """Shortcut wrapper that injects effective settings into page context."""
    if store is None:
        store = SettingsStore(db)
    effective_settings = await store.get_effective_dict()
    return await build_settings_page_context_svc(
        request,
        db,
        request_model=RequestModel,
        request_status_enum=RequestStatus,
        build_plex_job_statuses_func=_build_plex_job_statuses,
        effective_settings_override=effective_settings,
    )


async def _apply_runtime_setting(store: SettingsStore, key: str, value: str) -> None:
    """Persist *value* for *key* and update the runtime environment."""
    await store.set(key, value)
    env_name = ENV_KEY_MAP.get(key, key.upper())
    os.environ[env_name] = value
    reload_settings()


async def _run_bounded_with_progress(
    items: list[Any],
    limit: int,
    worker,
    *,
    on_event,
    phase: str,
) -> list[Any]:
    """Thin wrapper that injects router-level SSE progress builder."""
    return await run_bounded_with_progress_svc(
        items,
        limit,
        worker,
        on_event=on_event,
        phase=phase,
        build_sse_progress_func=build_sse_progress,
    )


async def _rescan_plex_tv_request(
    request_id: int,
    plex,
    runtime_settings,
) -> bool:
    """Thin wrapper that injects router-level session maker and logger."""
    return await rescan_plex_tv_request_svc(
        request_id,
        plex,
        runtime_settings,
        session_maker=db_mod.async_session_maker,
        logger=logger,
    )


async def _rescan_plex_requests(
    db,
    runtime_settings,
    plex,
    *,
    on_event=None,
    shallow: bool = False,
) -> tuple[int, int, int]:
    """Convenience wrapper that injects router-level service defaults."""
    return await rescan_plex_requests_svc(
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


async def _clear_runtime_settings(store: SettingsStore, *keys: str) -> None:
    """Remove *keys* from both DB and ``os.environ``."""
    await store.delete(*keys)
    for key in keys:
        env_name = ENV_KEY_MAP.get(key, key.upper())
        os.environ.pop(env_name, None)
    reload_settings()


# ── Page routes ────────────────────────────────────────────────────────────


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
    qbittorrent_api_key: str | None = Form(None),
    plex_url: str | None = Form(None),
    plex_token: str | None = Form(None),
    tz: str | None = Form(None),
) -> RedirectResponse:
    """Save connection settings as runtime environment overrides."""
    del request
    store = SettingsStore(db)
    await _apply_runtime_setting(store, "overseerr_url", overseerr_url or "")
    await _apply_runtime_setting(store, "overseerr_api_key", overseerr_api_key or "")
    await _apply_runtime_setting(store, "prowlarr_url", prowlarr_url or "")
    await _apply_runtime_setting(store, "prowlarr_api_key", prowlarr_api_key or "")
    await _apply_runtime_setting(store, "qbittorrent_url", qbittorrent_url or "")
    await _apply_runtime_setting(store, "qbittorrent_api_key", qbittorrent_api_key or "")
    await _apply_runtime_setting(store, "plex_url", plex_url or "")
    await _apply_runtime_setting(store, "plex_token", plex_token or "")
    if tz:
        await _apply_runtime_setting(store, "tz", tz)
    await db.commit()
    return RedirectResponse(url="/settings?saved=true", status_code=303)


@router.post("/connections/reset")
async def reset_connections(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    """Reset connection settings by clearing runtime environment overrides."""
    del request
    store = SettingsStore(db)
    await _clear_runtime_settings(
        store,
        "overseerr_url",
        "overseerr_api_key",
        "prowlarr_url",
        "prowlarr_api_key",
        "qbittorrent_url",
        "qbittorrent_api_key",
        "plex_url",
        "plex_token",
        "tz",
    )
    await db.commit()
    return RedirectResponse(url="/settings?reset=true", status_code=303)


# ── Connection testing API routes ─────────────────────────────────────────


@router.get("/api/connections", response_model=dict)
async def get_connections_api(db: AsyncSession = Depends(get_db)) -> dict:
    """Get current connection settings (for API)."""
    store = SettingsStore(db)
    effective = await store.get_effective_dict()
    return {
        "overseerr_url": effective["overseerr_url"],
        "overseerr_api_key": effective["overseerr_api_key"],
        "prowlarr_url": effective["prowlarr_url"],
        "prowlarr_api_key": effective["prowlarr_api_key"],
        "qbittorrent_url": effective["qbittorrent_url"],
        "qbittorrent_api_key": effective["qbittorrent_api_key"],
        "tz": effective["tz"],
    }


@router.post("/api/test/overseerr", response_model=ConnectionTestResponse)
async def test_overseerr_connection(db: AsyncSession = Depends(get_db)) -> ConnectionTestResponse:
    """Test connection to Overseerr."""
    result: ConnectionTestResult = await ConnectionTester.test_overseerr(get_settings())
    return ConnectionTestResponse(
        service="overseerr",
        success=result.success,
        message=result.message,
        details=result.details,
    )


@router.post("/api/test/prowlarr", response_model=ConnectionTestResponse)
async def test_prowlarr_connection(db: AsyncSession = Depends(get_db)) -> ConnectionTestResponse:
    """Test connection to Prowlarr."""
    result: ConnectionTestResult = await ConnectionTester.test_prowlarr(get_settings())
    return ConnectionTestResponse(
        service="prowlarr",
        success=result.success,
        message=result.message,
        details=result.details,
    )


@router.post("/api/test/qbittorrent", response_model=ConnectionTestResponse)
async def test_qbittorrent_connection(db: AsyncSession = Depends(get_db)) -> ConnectionTestResponse:
    """Test connection to qBittorrent."""
    result: ConnectionTestResult = await ConnectionTester.test_qbittorrent(get_settings())
    return ConnectionTestResponse(
        service="qbittorrent",
        success=result.success,
        message=result.message,
        details=result.details,
    )


@router.post("/api/test/plex", response_model=ConnectionTestResponse)
async def test_plex_connection(db: AsyncSession = Depends(get_db)) -> ConnectionTestResponse:
    """Test connection to Plex."""
    result: ConnectionTestResult = await ConnectionTester.test_plex(get_settings())
    return ConnectionTestResponse(
        service="plex",
        success=result.success,
        message=result.message,
        details=result.details,
    )


@router.post("/api/test/all", response_model=list[ConnectionTestResponse])
async def test_all_connections(db: AsyncSession = Depends(get_db)) -> list[ConnectionTestResponse]:
    """Test connections to all services."""
    effective_settings = get_settings()
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


# ── Plex rescan / sync routes ──────────────────────────────────────────────


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
        tv_resynced, tv_failed, completed = await _rescan_plex_requests(
            db,
            runtime_settings,
            plex,
        )

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
    staging_enabled = get_settings().staging_mode_enabled
    new_value = "false" if staging_enabled else "true"
    store = SettingsStore(db)
    await _apply_runtime_setting(store, "staging_mode_enabled", new_value)
    await db.commit()
    return RedirectResponse(url="/settings", status_code=303)


# ── Scheduler trigger routes ───────────────────────────────────────────────


@router.post("/retry-pending")
async def retry_pending(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Manually trigger retry of pending items."""
    scheduler_service = _get_scheduler_service()
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
    scheduler_service = _get_scheduler_service()
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
    scheduler_service = _get_scheduler_service()
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


# ── Maintenance routes ─────────────────────────────────────────────────────


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


# ── SSE streaming routes ───────────────────────────────────────────────────


@router.get("/api/rescan-plex/stream")
async def rescan_plex_stream(
    shallow: bool = False,
    mode: str | None = Query(default=None, pattern="^(partial|full)$"),
) -> StreamingResponse:
    """Stream Plex sync progress via SSE.

    The legacy shallow=true query remains compatible and maps to partial sync.
    """
    partial = shallow or mode == "partial"

    async def _inner() -> AsyncGenerator[str, None]:
        async for event in rescan_plex_generator_svc(
            shallow=partial,
            async_session_maker=db_mod.async_session_maker,
            plex_service_cls=PlexService,
            rescan_plex_requests_func=_rescan_plex_requests,
            build_sse_progress_func=build_sse_progress,
            logger=logger,
        ):
            yield event

    return StreamingResponse(
        _inner(),
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

    async def _inner() -> AsyncGenerator[str, None]:
        async for event in sync_overseerr_generator_svc(
            async_session_maker=db_mod.async_session_maker,
            build_effective_settings_func=build_effective_settings,
            import_overseerr_requests_func=import_overseerr_requests_svc,
            build_sse_progress_func=build_sse_progress,
            logger=logger,
            overseerr_service_cls=OverseerrService,
            plex_service_cls=PlexService,
            evaluate_imported_request_func=evaluate_imported_request,
            prepare_overseerr_import_func=prepare_overseerr_import,
        ):
            yield event

    return StreamingResponse(
        _inner(),
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
    store = SettingsStore(db)
    effective_settings = await store.get_effective_dict()
    context = await _build_settings_page_context(request, db, store=store)

    if not effective_settings.get("overseerr_url") or not effective_settings.get(
        "overseerr_api_key"
    ):
        context["message"] = "Overseerr is not configured. Please set URL and API key."
        context["message_type"] = "error"
        return templates.TemplateResponse(request, "settings.html", context)

    try:
        runtime_settings = get_settings()
        synced_count, skipped_count = await import_overseerr_requests_svc(
            db,
            runtime_settings,
            overseerr_service_cls=OverseerrService,
            plex_service_cls=PlexService,
            evaluate_imported_request_func=evaluate_imported_request,
            prepare_overseerr_import_func=prepare_overseerr_import,
            logger=logger,
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
