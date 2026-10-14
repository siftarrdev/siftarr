"""Consolidated settings router."""

import json
import logging
import os
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from fastapi.responses import (
    HTMLResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.siftarr.config import (
    generate_api_key,
    get_settings,
    normalize_full_sync_frequency,
    normalize_full_sync_time,
    reload_settings,
)
from app.siftarr.database import get_db
from app.siftarr.services.admin.settings_service import (
    ENV_KEY_MAP,
    SettingsBackupError,
    SettingsStore,
    build_manual_plex_job_message,
    import_overseerr_requests,
    mask_secret,
    record_initial_plex_sync_completion,
    rescan_plex_generator,
    rescan_plex_requests,
    sync_overseerr_generator,
)
from app.siftarr.services.admin.settings_service import (
    build_settings_page_context as build_settings_page_context_svc,
)
from app.siftarr.services.decisions.rule_service import RuleService
from app.siftarr.services.integrations.connection_tester import (
    ConnectionTester,
    ConnectionTestResult,
)
from app.siftarr.services.integrations.plex_service import PlexService
from app.siftarr.services.releases.release_storage import clear_release_search_cache
from app.siftarr.services.utils.sse import sse_response
from app.siftarr.templating import configure_templates

router = APIRouter(prefix="/settings", tags=["settings"])
templates = configure_templates(Jinja2Templates(directory="app/siftarr/templates"))
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


class ApiKeyResponse(BaseModel):
    """Response model for API key operations."""

    api_key: str
    api_key_masked: str


# ── Convenience helpers (not pass-through; provide router-level defaults) ──


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
        effective_settings=effective_settings,
    )


async def _apply_runtime_setting(store: SettingsStore, key: str, value: str) -> None:
    """Persist *value* for *key* and update the runtime environment."""
    await store.set(key, value)
    env_name = ENV_KEY_MAP.get(key, key.upper())
    os.environ[env_name] = value
    reload_settings()


def _coerce_positive_int(value: str | None, default: int, *, minimum: int = 1) -> str:
    try:
        parsed = int(str(value or "").strip())
    except ValueError:
        parsed = default
    return str(max(parsed, minimum))


def _coerce_bool_form(value: str | None) -> str:
    return "true" if str(value or "").lower() in {"1", "true", "on", "yes"} else "false"


def _qbit_move_message(result) -> tuple[str, str]:
    if result.status == "locked":
        return "qBittorrent move/retention is already running.", "error"
    if result.status == "error":
        return f"qBittorrent move/retention failed: {result.error}", "error"
    return (
        "qBittorrent move/retention completed. "
        f"Moved {result.moved}, removed {result.removed}, errors {result.errors}.",
        "success" if result.errors == 0 else "error",
    )


def _restart_scheduler_if_running() -> None:
    scheduler_service = _get_scheduler_service()
    if scheduler_service is not None and scheduler_service.scheduler is not None:
        scheduler_service.stop()
        scheduler_service.start()


async def _clear_runtime_settings(store: SettingsStore, *keys: str) -> None:
    """Remove *keys* from both DB and ``os.environ``."""
    await store.delete(*keys)
    for key in keys:
        env_name = ENV_KEY_MAP.get(key, key.upper())
        os.environ.pop(env_name, None)
    reload_settings()


async def _get_runtime_settings(db: AsyncSession):
    """Return settings after applying DB overrides to the runtime environment."""
    await SettingsStore(db).load_into_environ()
    return get_settings()


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

    # Add Plex SSO status without exposing the stored Plex token.
    effective = context["env"]
    context["plex_sso_username"] = effective.get("plex_username")
    context["plex_sso_thumb"] = effective.get("plex_thumb")
    context["plex_sso_token_present"] = bool(effective.get("plex_token_present"))
    context["plex_sso_connected"] = context["plex_sso_username"] is not None

    # Add API key (plaintext for the settings page)
    context["api_key"] = get_settings().api_key

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
    tz: str | None = Form(None),
) -> RedirectResponse:
    """Save connection settings as runtime environment overrides."""
    del request
    store = SettingsStore(db)
    connection_values = {
        "overseerr_url": overseerr_url,
        "overseerr_api_key": overseerr_api_key,
        "prowlarr_url": prowlarr_url,
        "prowlarr_api_key": prowlarr_api_key,
        "qbittorrent_url": qbittorrent_url,
        "qbittorrent_api_key": qbittorrent_api_key,
        "plex_url": plex_url,
    }
    for key, value in connection_values.items():
        await _apply_runtime_setting(store, key, value or "")
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
        "tz",
    )
    await db.commit()
    return RedirectResponse(url="/settings?reset=true", status_code=303)


@router.post("/scheduler")
async def save_scheduler_settings(
    request: Request,
    db: AsyncSession = Depends(get_db),
    overseerr_poll_interval_minutes: str | None = Form(None),
    qbittorrent_completion_poll_interval_seconds: str | None = Form(None),
    plex_fast_sync_interval_minutes: str | None = Form(None),
    plex_full_sync_frequency: str | None = Form(None),
    plex_full_sync_time: str | None = Form(None),
) -> RedirectResponse:
    """Save scheduler runtime settings."""
    del request
    current = get_settings()
    store = SettingsStore(db)
    await _apply_runtime_setting(
        store,
        "overseerr_poll_interval_minutes",
        _coerce_positive_int(
            overseerr_poll_interval_minutes, current.overseerr_poll_interval_minutes
        ),
    )
    await _apply_runtime_setting(
        store,
        "qbittorrent_completion_poll_interval_seconds",
        _coerce_positive_int(
            qbittorrent_completion_poll_interval_seconds,
            current.qbittorrent_completion_poll_interval_seconds,
            minimum=5,
        ),
    )
    await _apply_runtime_setting(
        store,
        "plex_fast_sync_interval_minutes",
        _coerce_positive_int(
            plex_fast_sync_interval_minutes, current.plex_fast_sync_interval_minutes
        ),
    )
    await _apply_runtime_setting(
        store,
        "plex_full_sync_frequency",
        normalize_full_sync_frequency(plex_full_sync_frequency),
    )
    await _apply_runtime_setting(
        store, "plex_full_sync_time", normalize_full_sync_time(plex_full_sync_time)
    )
    await db.commit()
    _restart_scheduler_if_running()
    return RedirectResponse(url="/settings?scheduler_saved=true", status_code=303)


@router.post("/scheduler/reset")
async def reset_scheduler_settings(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    """Reset scheduler runtime settings to configured defaults."""
    del request
    store = SettingsStore(db)
    await _clear_runtime_settings(
        store,
        "overseerr_poll_interval_minutes",
        "qbittorrent_completion_poll_interval_seconds",
        "plex_fast_sync_interval_minutes",
        "plex_full_sync_frequency",
        "plex_full_sync_time",
    )
    await db.commit()
    _restart_scheduler_if_running()
    return RedirectResponse(url="/settings?scheduler_reset=true", status_code=303)


@router.post("/qbit-move")
async def save_qbit_move_settings(
    request: Request,
    db: AsyncSession = Depends(get_db),
    qbittorrent_move_enabled: str | None = Form(None),
    qbittorrent_move_completed_dir: str | None = Form(None),
    qbittorrent_move_movie_root: str | None = Form(None),
    qbittorrent_move_tv_root: str | None = Form(None),
    qbittorrent_move_unmanaged_fallback_enabled: str | None = Form(None),
    qbittorrent_move_retention_weeks: str | None = Form(None),
) -> RedirectResponse:
    """Save qBittorrent move/retention settings without restarting scheduler jobs."""
    del request
    current = get_settings()
    store = SettingsStore(db)
    await _apply_runtime_setting(
        store, "qbittorrent_move_enabled", _coerce_bool_form(qbittorrent_move_enabled)
    )
    await _apply_runtime_setting(
        store,
        "qbittorrent_move_completed_dir",
        (qbittorrent_move_completed_dir or current.qbittorrent_move_completed_dir).strip(),
    )
    await _apply_runtime_setting(
        store,
        "qbittorrent_move_movie_root",
        (qbittorrent_move_movie_root or current.qbittorrent_move_movie_root).strip(),
    )
    await _apply_runtime_setting(
        store,
        "qbittorrent_move_tv_root",
        (qbittorrent_move_tv_root or current.qbittorrent_move_tv_root).strip(),
    )
    await _apply_runtime_setting(
        store,
        "qbittorrent_move_unmanaged_fallback_enabled",
        _coerce_bool_form(qbittorrent_move_unmanaged_fallback_enabled),
    )
    await _apply_runtime_setting(
        store,
        "qbittorrent_move_retention_weeks",
        _coerce_positive_int(
            qbittorrent_move_retention_weeks,
            current.qbittorrent_move_retention_weeks,
        ),
    )
    await db.commit()
    return RedirectResponse(url="/settings?qbit_move_saved=true", status_code=303)


# ── Settings backup / restore ──────────────────────────────────────────────


@router.get("/backup/export")
async def export_settings_backup(db: AsyncSession = Depends(get_db)) -> Response:
    """Download a non-secret settings backup."""
    backup = await SettingsStore(db).export_backup()
    return Response(
        content=json.dumps(backup, indent=2),
        media_type="application/json",
        headers={"Content-Disposition": "attachment; filename=siftarr-settings-backup.json"},
    )


@router.post("/backup/preview")
async def preview_settings_backup(
    request: Request,
    db: AsyncSession = Depends(get_db),
    backup_file: UploadFile = File(...),
) -> HTMLResponse:
    """Preview a settings backup without applying it."""
    context = await _build_settings_page_context(request, db)
    try:
        payload = await backup_file.read()
        context["settings_backup_preview"] = await SettingsStore(db).preview_backup(payload)
        context["message"] = "Settings backup preview loaded. Review before restoring."
        context["message_type"] = "success"
    except SettingsBackupError as exc:
        context["message"] = str(exc)
        context["message_type"] = "error"
    return templates.TemplateResponse(request, "settings.html", context)


@router.post("/backup/restore")
async def restore_settings_backup(
    request: Request,
    db: AsyncSession = Depends(get_db),
    mode: str = Form("update"),
    backup_file: UploadFile = File(...),
) -> HTMLResponse:
    """Apply a settings backup using update or replace mode."""
    context = await _build_settings_page_context(request, db)
    try:
        result = await SettingsStore(db).restore_backup(await backup_file.read(), mode=mode)
        await db.commit()
        context = await _build_settings_page_context(request, db)
        context["message"] = f"Restored {result['restored']} setting(s)."
        context["message_type"] = "success"
    except SettingsBackupError as exc:
        context["message"] = str(exc)
        context["message_type"] = "error"
    return templates.TemplateResponse(request, "settings.html", context)


# ── Connection testing API routes ─────────────────────────────────────────


def _connection_testers() -> dict[str, Callable[[Any], Awaitable[ConnectionTestResult]]]:
    """Return the service testers in the order "test all" runs them.

    Built per call so the current ``ConnectionTester`` attributes are read.
    """
    return {
        "plex": ConnectionTester.test_plex,
        "overseerr": ConnectionTester.test_overseerr,
        "prowlarr": ConnectionTester.test_prowlarr,
        "qbittorrent": ConnectionTester.test_qbittorrent,
    }


def _to_response(service: str, result: ConnectionTestResult) -> ConnectionTestResponse:
    return ConnectionTestResponse(
        service=service,
        success=result.success,
        message=result.message,
        details=result.details,
    )


async def _test_connection(service: str, db: AsyncSession) -> ConnectionTestResponse:
    tester = _connection_testers()[service]
    return _to_response(service, await tester(await _get_runtime_settings(db)))


@router.post("/api/test/overseerr", response_model=ConnectionTestResponse)
async def test_overseerr_connection(db: AsyncSession = Depends(get_db)) -> ConnectionTestResponse:
    """Test connection to Overseerr."""
    return await _test_connection("overseerr", db)


@router.post("/api/test/prowlarr", response_model=ConnectionTestResponse)
async def test_prowlarr_connection(db: AsyncSession = Depends(get_db)) -> ConnectionTestResponse:
    """Test connection to Prowlarr."""
    return await _test_connection("prowlarr", db)


@router.post("/api/test/qbittorrent", response_model=ConnectionTestResponse)
async def test_qbittorrent_connection(db: AsyncSession = Depends(get_db)) -> ConnectionTestResponse:
    """Test connection to qBittorrent."""
    return await _test_connection("qbittorrent", db)


@router.post("/api/test/plex", response_model=ConnectionTestResponse)
async def test_plex_connection(db: AsyncSession = Depends(get_db)) -> ConnectionTestResponse:
    """Test connection to Plex."""
    return await _test_connection("plex", db)


@router.post("/api/test/all", response_model=list[ConnectionTestResponse])
async def test_all_connections(db: AsyncSession = Depends(get_db)) -> list[ConnectionTestResponse]:
    """Test connections to all services."""
    effective_settings = await _get_runtime_settings(db)
    return [
        _to_response(service_name, await tester(effective_settings))
        for service_name, tester in _connection_testers().items()
    ]


# ── API Key management ────────────────────────────────────────────────────


@router.get("/api/api-key", response_model=ApiKeyResponse)
async def get_api_key() -> ApiKeyResponse:
    """Get the current API key."""
    settings = get_settings()
    return ApiKeyResponse(
        api_key=settings.api_key,
        api_key_masked=mask_secret(settings.api_key) or "",
    )


@router.post("/api/api-key/regenerate", response_model=ApiKeyResponse)
async def regenerate_api_key(db: AsyncSession = Depends(get_db)) -> ApiKeyResponse:
    """Generate a new API key and persist it."""
    new_key = generate_api_key()
    await _apply_runtime_setting(SettingsStore(db), "api_key", new_key)
    await db.commit()
    return ApiKeyResponse(
        api_key=new_key,
        api_key_masked=mask_secret(new_key) or "",
    )


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
        tv_resynced, tv_failed, completed = await rescan_plex_requests(
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
    return templates.TemplateResponse(request, "settings.html", context)


@router.post("/run-qbit-move")
async def run_qbit_move(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Manually trigger guarded qBittorrent completion/move/retention workflow."""
    scheduler_service = _get_scheduler_service()
    context = await _build_settings_page_context(request, db)
    if scheduler_service is None:
        context["message"] = "Scheduler not available"
        context["message_type"] = "error"
        return templates.TemplateResponse(request, "settings.html", context)

    result = await scheduler_service.trigger_download_completion_now()
    context["message"], context["message_type"] = _qbit_move_message(result)
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
    request: Request,
    shallow: bool = False,
    mode: str | None = Query(default=None, pattern="^(partial|full)$"),
) -> StreamingResponse:
    """Stream Plex sync progress via SSE.

    The legacy shallow=true query remains compatible and maps to partial sync.
    """
    partial = shallow or mode == "partial"
    gate_id = request.session.get("initial_plex_sync_gate_id")
    user_id = request.session.get("plex_user_id")

    def mark_initial_gate_complete() -> None:
        record_initial_plex_sync_completion(gate_id, user_id)

    return sse_response(
        rescan_plex_generator(
            shallow=partial,
            on_full_sync_complete=mark_initial_gate_complete,
        )
    )


@router.get("/api/sync-overseerr/stream")
async def sync_overseerr_stream() -> StreamingResponse:
    """Stream Overseerr sync progress via SSE."""

    return sse_response(sync_overseerr_generator())


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
        synced_count, skipped_count = await import_overseerr_requests(
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
