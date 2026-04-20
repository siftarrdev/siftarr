"""Settings router package with compatibility re-exports."""

from collections.abc import AsyncGenerator
from importlib import import_module
from typing import Any

from app.siftarr.database import async_session_maker
from app.siftarr.models.request import Request as RequestModel
from app.siftarr.models.request import RequestStatus
from app.siftarr.models.settings import Settings as DBSettings
from app.siftarr.services.connection_tester import ConnectionTester
from app.siftarr.services.overseerr_service import OverseerrService
from app.siftarr.services.pending_queue_service import PendingQueueService
from app.siftarr.services.plex_polling_service import PlexPollingService
from app.siftarr.services.plex_scan_state_service import PlexScanStateService
from app.siftarr.services.plex_service import PlexService
from app.siftarr.services.release_selection_service import clear_release_search_cache
from app.siftarr.services.rule_service import RuleService
from app.siftarr.services.runtime_settings import get_effective_settings
from app.siftarr.services.scheduler_service import (
    PLEX_FULL_RECONCILE_JOB_NAME,
    PLEX_INCREMENTAL_SYNC_JOB_NAME,
)
from app.siftarr.services.settings import overseerr_import as overseerr_import_service
from app.siftarr.services.settings import page_context as page_context_service
from app.siftarr.services.settings import plex_jobs as plex_jobs_service
from app.siftarr.services.settings import plex_rescan as plex_rescan_service
from app.siftarr.services.settings import sse as sse_service
from app.siftarr.services.unreleased_service import evaluate_imported_request

from .schemas import ConnectionSettings, ConnectionTestResponse
from .shared import logger, router, templates


def _serialize_datetime(value):
    return plex_jobs_service.serialize_datetime(value)


def _build_compact_metrics_snapshot(metrics_payload: dict[str, Any] | None) -> str | None:
    return plex_jobs_service.build_compact_metrics_snapshot(metrics_payload)


def _build_plex_run_outcome_summary(
    metrics_payload: dict[str, Any] | None,
    *,
    locked: bool = False,
    lock_owner: str | None = None,
    last_error: str | None = None,
) -> str | None:
    return plex_jobs_service.build_plex_run_outcome_summary(
        metrics_payload,
        locked=locked,
        lock_owner=lock_owner,
        last_error=last_error,
    )


def _build_manual_plex_job_message(job_label: str, result: Any) -> tuple[str, str]:
    return plex_jobs_service.build_manual_plex_job_message(job_label, result)


async def _build_plex_job_statuses(db) -> list[dict[str, Any]]:
    return await plex_jobs_service.build_plex_job_statuses(
        db,
        plex_scan_state_service_cls=PlexScanStateService,
        incremental_job_name=PLEX_INCREMENTAL_SYNC_JOB_NAME,
        full_job_name=PLEX_FULL_RECONCILE_JOB_NAME,
    )


def _build_sse_progress(
    phase: str,
    *,
    current: int | None = None,
    total: int | None = None,
    title: str | None = None,
    active: list[str] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    return sse_service.build_sse_progress(
        phase,
        current=current,
        total=total,
        title=title,
        active=active,
        **extra,
    )


async def _run_bounded_with_progress(
    items: list[Any],
    limit: int,
    worker,
    *,
    on_event,
    phase: str,
) -> list[Any]:
    return await sse_service.run_bounded_with_progress(
        items,
        limit,
        worker,
        on_event=on_event,
        phase=phase,
        build_sse_progress_func=_build_sse_progress,
    )


async def _set_db_setting(db, key: str, value: str, description: str | None = None) -> None:
    await page_context_service.set_db_setting(
        db,
        key,
        value,
        description,
        settings_model=DBSettings,
    )


async def _build_effective_settings(db) -> dict[str, Any]:
    return await page_context_service.build_effective_settings(
        db,
        get_effective_settings_func=get_effective_settings,
    )


async def _build_effective_settings_obj(db):
    return await page_context_service.build_effective_settings_obj(
        db,
        build_effective_settings_func=_build_effective_settings,
    )


async def _build_settings_page_context(request, db) -> dict[str, Any]:
    return await page_context_service.build_settings_page_context(
        request,
        db,
        build_effective_settings_func=_build_effective_settings,
        settings_model=DBSettings,
        pending_queue_service_cls=PendingQueueService,
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
    return await overseerr_import_service.prepare_overseerr_import(
        ov_req,
        overseerr_service,
        semaphore,
        media_details_tasks,
        media_details_lock,
    )


async def _import_overseerr_requests(db, runtime_settings) -> tuple[int, int]:
    return await overseerr_import_service.import_overseerr_requests(
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
    force_plex_refresh: bool = True,
) -> bool:
    return await plex_rescan_service.rescan_plex_tv_request(
        request_id,
        plex,
        runtime_settings,
        session_maker=async_session_maker,
        logger=logger,
        force_plex_refresh=force_plex_refresh,
    )


async def _rescan_plex_requests(
    db,
    runtime_settings,
    plex,
    *,
    on_event=None,
    shallow: bool = False,
) -> tuple[int, int, int]:
    return await plex_rescan_service.rescan_plex_requests(
        db,
        runtime_settings,
        plex,
        on_event=on_event,
        shallow=shallow,
        plex_polling_service_cls=PlexPollingService,
        build_sse_progress_func=_build_sse_progress,
        run_bounded_with_progress_func=_run_bounded_with_progress,
        rescan_plex_tv_request_func=_rescan_plex_tv_request,
    )


async def _sync_overseerr_generator() -> AsyncGenerator[str, None]:
    async for event in overseerr_import_service.sync_overseerr_generator(
        async_session_maker=async_session_maker,
        build_effective_settings_func=_build_effective_settings,
        get_effective_settings_func=get_effective_settings,
        import_overseerr_requests_func=_import_overseerr_requests,
        build_sse_progress_func=_build_sse_progress,
        logger=logger,
    ):
        yield event


async def _rescan_plex_generator(shallow: bool = False) -> AsyncGenerator[str, None]:
    async for event in plex_rescan_service.rescan_plex_generator(
        shallow=shallow,
        async_session_maker=async_session_maker,
        get_effective_settings_func=get_effective_settings,
        plex_service_cls=PlexService,
        rescan_plex_requests_func=_rescan_plex_requests,
        build_sse_progress_func=_build_sse_progress,
        logger=logger,
    ):
        yield event


_connections = import_module(".connections", __name__)
_imports = import_module(".imports", __name__)
_jobs = import_module(".jobs", __name__)
_maintenance = import_module(".maintenance", __name__)
_page = import_module(".page", __name__)

get_connections_api = _connections.get_connections_api
reset_connections = _connections.reset_connections
save_connections = _connections.save_connections
test_all_connections = _connections.test_all_connections
test_overseerr_connection = _connections.test_overseerr_connection
test_plex_connection = _connections.test_plex_connection
test_prowlarr_connection = _connections.test_prowlarr_connection
test_qbittorrent_connection = _connections.test_qbittorrent_connection

rescan_plex_stream = _imports.rescan_plex_stream
sync_overseerr = _imports.sync_overseerr
sync_overseerr_stream = _imports.sync_overseerr_stream

rescan_plex = _jobs.rescan_plex
retry_pending = _jobs.retry_pending
run_full_plex_reconcile = _jobs.run_full_plex_reconcile
run_incremental_plex_sync = _jobs.run_incremental_plex_sync
toggle_staging_mode = _jobs.toggle_staging_mode

clear_cache = _maintenance.clear_cache
reseed_rules = _maintenance.reseed_rules

get_settings_page = _page.get_settings_page


__all__ = [
    "ConnectionSettings",
    "ConnectionTestResponse",
    "ConnectionTester",
    "OverseerrService",
    "PendingQueueService",
    "PlexPollingService",
    "PlexScanStateService",
    "PlexService",
    "RuleService",
    "async_session_maker",
    "clear_cache",
    "clear_release_search_cache",
    "evaluate_imported_request",
    "get_connections_api",
    "get_effective_settings",
    "get_settings_page",
    "logger",
    "rescan_plex",
    "rescan_plex_stream",
    "reseed_rules",
    "reset_connections",
    "retry_pending",
    "router",
    "run_full_plex_reconcile",
    "run_incremental_plex_sync",
    "save_connections",
    "sync_overseerr",
    "sync_overseerr_stream",
    "templates",
    "test_all_connections",
    "test_overseerr_connection",
    "test_plex_connection",
    "test_prowlarr_connection",
    "test_qbittorrent_connection",
    "toggle_staging_mode",
    "_build_compact_metrics_snapshot",
    "_build_effective_settings",
    "_build_effective_settings_obj",
    "_build_manual_plex_job_message",
    "_build_plex_job_statuses",
    "_build_plex_run_outcome_summary",
    "_build_settings_page_context",
    "_build_sse_progress",
    "_import_overseerr_requests",
    "_prepare_overseerr_import",
    "_rescan_plex_generator",
    "_rescan_plex_requests",
    "_rescan_plex_tv_request",
    "_run_bounded_with_progress",
    "_serialize_datetime",
    "_set_db_setting",
    "_sync_overseerr_generator",
]
