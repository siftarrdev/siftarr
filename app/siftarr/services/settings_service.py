"""Consolidated settings service helpers."""

import asyncio
import contextlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.siftarr.config import Settings, get_settings
from app.siftarr.models.request import MediaType, RequestStatus
from app.siftarr.models.request import Request as RequestModel
from app.siftarr.services.pending_queue_service import PendingQueueService


async def build_effective_settings() -> dict[str, Any]:
    """Build the effective flattened settings payload."""
    effective = get_settings()
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


async def build_effective_settings_obj(db: AsyncSession) -> Settings:
    """Build the effective Settings object."""
    del db
    return get_settings()


async def build_settings_page_context(
    request,
    db: AsyncSession,
    *,
    request_model,
    request_status_enum,
    build_plex_job_statuses_func,
) -> dict[str, Any]:
    """Build the shared context required by the settings page."""
    effective_settings = await build_effective_settings()

    staging_enabled = get_settings().staging_mode_enabled

    queue_service = PendingQueueService(db)
    pending_count = len(await queue_service.get_ready_for_retry())

    status_counts = (
        await db.execute(select(request_model.status, func.count()).group_by(request_model.status))
    ).all()
    stats_by_status = {status.value: count for status, count in status_counts}

    return {
        "request": request,
        "staging_enabled": staging_enabled,
        "pending_count": pending_count,
        "stats": {
            "total_requests": sum(stats_by_status.values()),
            "completed": stats_by_status.get(request_status_enum.COMPLETED.value, 0),
            "pending": stats_by_status.get(request_status_enum.PENDING.value, 0),
            "failed": stats_by_status.get(request_status_enum.FAILED.value, 0),
        },
        "plex_jobs": await build_plex_job_statuses_func(db),
        "env": effective_settings,
    }


def serialize_datetime(value: datetime | None) -> str | None:
    """Serialize datetimes for compact status rendering."""
    return value.isoformat(sep=" ", timespec="seconds") if value is not None else None


def build_compact_metrics_snapshot(metrics_payload: dict[str, Any] | None) -> str | None:
    """Render a compact operator-facing metrics summary."""
    if not isinstance(metrics_payload, dict):
        return None

    parts: list[str] = []
    if "completed_requests" in metrics_payload:
        parts.append(f"completed={metrics_payload['completed_requests']}")
    for source_key, label in [
        ("scanned_items", "scanned"),
        ("matched_requests", "matched"),
        ("skipped_on_error_items", "errors"),
    ]:
        value = metrics_payload.get(source_key)
        if value is not None:
            parts.append(f"{label}={value}")

    return ", ".join(parts) if parts else None


def _coerce_int(value: Any) -> int:
    return value if isinstance(value, int) else 0


def build_plex_run_outcome_summary(
    metrics_payload: dict[str, Any] | None,
    *,
    locked: bool = False,
    lock_owner: str | None = None,
    last_error: str | None = None,
) -> str | None:
    """Summarize the last known operator-facing outcome for a Plex job."""
    if locked:
        return f"Skipped due to lock ({lock_owner or 'another worker'})"

    if not isinstance(metrics_payload, dict):
        return "No completed run recorded"

    scanned = metrics_payload.get("scanned_items")
    matched = metrics_payload.get("matched_requests")
    skipped = _coerce_int(metrics_payload.get("skipped_on_error_items"))
    completed = _coerce_int(metrics_payload.get("completed_requests"))

    if scanned is not None:
        if skipped or last_error:
            return (
                "Recent scan partial; "
                f"completed {completed}, matched {matched or 0}, scanned {scanned}, errors {max(skipped, 1)}"
            )
        return f"Recent scan completed; completed {completed}, matched {matched or 0}, scanned {scanned}"

    return f"Plex poll completed; transitioned {completed} request(s)"


def build_manual_plex_job_message(job_label: str, result: Any) -> tuple[str, str]:
    """Build a concise manual-trigger status message for Plex jobs."""
    if result.status == "locked":
        return f"{job_label} is already in progress.", "error"
    if result.status != "completed":
        return f"{job_label} failed: {result.error}", "error"

    message = f"{job_label} completed. Transitioned {result.completed_requests} request(s)."
    outcome_summary = build_plex_run_outcome_summary(result.metrics_payload)
    if outcome_summary and outcome_summary.startswith("Recent scan completed;"):
        message = (
            f"{job_label} completed. Transitioned {result.completed_requests} request(s). "
            f"{outcome_summary}."
        )
    elif outcome_summary and outcome_summary.startswith("Recent scan partial;"):
        message = (
            f"{job_label} completed partially. "
            f"Transitioned {result.completed_requests} request(s). "
            f"{outcome_summary.removeprefix('Recent scan partial; ').capitalize()}."
        )
    elif outcome_summary and outcome_summary.startswith("Plex poll completed;"):
        message = (
            f"{job_label} completed. "
            f"{outcome_summary.removeprefix('Plex poll completed; ').capitalize()}."
        )

    return message, "success"


async def build_plex_job_statuses(
    db: AsyncSession,
    *,
    recent_scan_job_name: str,
    poll_job_name: str,
) -> list[dict[str, Any]]:
    """Load in-memory scheduler status for Plex scan jobs."""
    del db
    from app.siftarr.main import scheduler_service

    job_rows = [
        (recent_scan_job_name, "Recent Plex Scan", "Recent-additions scan for active requests"),
        (poll_job_name, "Plex Poll", "Active-request availability poll"),
    ]
    job_state = (
        await scheduler_service.get_plex_job_state_snapshot()
        if scheduler_service is not None
        else {}
    )

    statuses: list[dict[str, Any]] = []
    for job_name, label, description in job_rows:
        state = job_state.get(job_name, {})
        metrics_payload = state.get("metrics_payload")
        locked = bool(state.get("locked", False))
        lock_owner = state.get("lock_owner")
        last_error = state.get("last_error")
        statuses.append(
            {
                "job_name": job_name,
                "label": label,
                "description": description,
                "last_success": serialize_datetime(state.get("last_success")),
                "last_run": serialize_datetime(state.get("last_run")),
                "last_started": serialize_datetime(state.get("last_started")),
                "locked": locked,
                "lock_owner": lock_owner,
                "last_error": last_error,
                "run_summary": build_plex_run_outcome_summary(
                    metrics_payload,
                    locked=locked,
                    lock_owner=lock_owner,
                    last_error=last_error,
                ),
                "metrics_snapshot": build_compact_metrics_snapshot(metrics_payload),
                "metrics_payload": metrics_payload,
            }
        )
    return statuses


def build_sse_progress(
    phase: str,
    *,
    current: int | None = None,
    total: int | None = None,
    title: str | None = None,
    active: list[str] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """Build a progress payload for SSE consumers."""
    payload: dict[str, Any] = {"phase": phase}
    if current is not None:
        payload["current"] = current
    if total is not None:
        payload["total"] = total
    if title is not None:
        payload["title"] = title
    if active is not None:
        payload["active"] = active[:16]
    payload.update(extra)
    return payload


def serialize_sse(data: dict[str, Any]) -> str:
    """Serialize a payload as an SSE event."""
    return f"data: {json.dumps(data)}\n\n"


async def run_bounded_with_progress(
    items: list[Any],
    limit: int,
    worker,
    *,
    on_event,
    phase: str,
    build_sse_progress_func,
) -> list[Any]:
    """Run async work with bounded concurrency and progress callbacks."""
    total = len(items)
    semaphore = asyncio.Semaphore(max(1, limit))
    active_titles: list[str] = []
    active_lock = asyncio.Lock()
    started = 0
    finished = 0

    async def emit(payload: dict[str, Any]) -> None:
        result = on_event(payload)
        if asyncio.iscoroutine(result):
            await result

    if total == 0:
        await emit(
            build_sse_progress_func(
                phase,
                current=0,
                total=1,
                started=0,
                completed=0,
                active=[],
            )
        )
        return []

    async def run(item: Any) -> Any:
        nonlocal started, finished
        title = getattr(item, "title", None) or f"Request #{getattr(item, 'id', '?')}"

        async with semaphore:
            async with active_lock:
                started += 1
                active_titles.append(title)
                active_snapshot = active_titles[:16]

            await emit(
                build_sse_progress_func(
                    phase,
                    current=finished,
                    total=total,
                    started=started,
                    completed=finished,
                    title=title,
                    active=active_snapshot,
                )
            )

            try:
                return await worker(item)
            finally:
                async with active_lock:
                    with contextlib.suppress(ValueError):
                        active_titles.remove(title)
                    finished += 1
                    active_snapshot = active_titles[:16]

                await emit(
                    build_sse_progress_func(
                        phase,
                        current=finished,
                        total=total,
                        started=started,
                        completed=finished,
                        title=title,
                        active=active_snapshot,
                    )
                )

    return await asyncio.gather(*(run(item) for item in items))


async def rescan_plex_tv_request(
    request_id: int,
    plex,
    runtime_settings,
    *,
    session_maker,
    logger,
) -> bool:
    """Resync one TV request on an isolated DB session."""
    from app.siftarr.services.episode_sync_service import EpisodeSyncService
    from app.siftarr.services.overseerr_service import OverseerrService

    async with session_maker() as worker_db:
        overseerr = OverseerrService(settings=runtime_settings)
        episode_sync = EpisodeSyncService(worker_db, overseerr=overseerr, plex=plex)
        try:
            await episode_sync.sync_request(request_id)
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


async def rescan_plex_requests(
    db,
    runtime_settings,
    plex,
    *,
    on_event=None,
    shallow: bool = False,
    plex_polling_service_cls,
    build_sse_progress_func,
    run_bounded_with_progress_func,
    rescan_plex_tv_request_func,
) -> tuple[int, int, int]:
    """Run the manual Plex rescan path."""
    mode = "partial" if shallow else "full"
    polling_service = plex_polling_service_cls(db, plex)
    active_requests = await polling_service.get_active_requests()
    active_requests = [req for req in active_requests if req.status != RequestStatus.COMPLETED]

    def is_completed_status(value: Any) -> bool:
        if value == RequestStatus.COMPLETED:
            return True
        return getattr(value, "value", value) == RequestStatus.COMPLETED.value

    def title_for(req: RequestModel) -> str:
        return req.title or f"Request #{req.id}"

    tv_requests = [req for req in active_requests if req.media_type == MediaType.TV]

    if shallow:

        def all_episodes_available(req: RequestModel) -> bool:
            seasons = list(getattr(req, "seasons", []) or [])
            if not seasons:
                return False
            for season in seasons:
                episodes = list(getattr(season, "episodes", []) or [])
                if not episodes:
                    return False
                for episode in episodes:
                    if not is_completed_status(episode.status):
                        return False
            return True

        tv_requests = [req for req in tv_requests if not all_episodes_available(req)]

    configured_concurrency = getattr(runtime_settings, "plex_sync_concurrency", 1)
    sync_concurrency = (
        configured_concurrency
        if isinstance(configured_concurrency, int) and configured_concurrency > 0
        else 1
    )

    if on_event is not None:
        await on_event(
            build_sse_progress_func(
                "fetching",
                current=0,
                total=max(1, len(active_requests)),
                title=(
                    "Finding new or incomplete Plex content..."
                    if shallow
                    else "Finding active Plex requests for full sync..."
                ),
                active=[
                    title_for(req) for req in (tv_requests if shallow else active_requests)[:16]
                ],
                mode=mode,
                message=(
                    "Partial Plex sync: checking new or incomplete TV content only."
                    if shallow
                    else "Full Plex sync: refreshing active non-completed TV metadata."
                ),
            )
        )

    async def resync_worker(request: RequestModel) -> bool:
        return await rescan_plex_tv_request_func(
            request.id,
            plex,
            runtime_settings,
        )

    if tv_requests:
        resync_results = await run_bounded_with_progress_func(
            tv_requests,
            sync_concurrency,
            resync_worker,
            on_event=on_event or (lambda _payload: None),
            phase="processing",
        )
    else:
        resync_results = []

    tv_resynced = sum(1 for result in resync_results if result)
    tv_failed = len(resync_results) - tv_resynced

    if on_event is not None:
        await on_event(
            build_sse_progress_func(
                "polling",
                current=0,
                total=1,
                title=(
                    "Polling Plex availability after partial sync..."
                    if shallow
                    else "Refreshing metadata and polling Plex availability..."
                ),
                active=[],
                mode=mode,
                message=(
                    "Polling Plex availability for active requests after partial sync..."
                    if shallow
                    else "Running full Plex metadata refresh and availability poll..."
                ),
            )
        )

    completed = await polling_service.poll(on_progress=on_event)
    return tv_resynced, tv_failed, completed


async def rescan_plex_generator(
    *,
    shallow: bool = False,
    async_session_maker,
    plex_service_cls,
    rescan_plex_requests_func,
    build_sse_progress_func,
    logger,
):
    """Yield SSE events for Plex re-scan progress."""
    mode = "partial" if shallow else "full"
    try:
        yield serialize_sse({"phase": "connecting", "mode": mode})

        async with async_session_maker() as db:
            runtime_settings = get_settings()
            plex = plex_service_cls(settings=runtime_settings)
            try:
                queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

                async def emit(payload: dict[str, Any]) -> None:
                    await queue.put(payload)

                task = asyncio.create_task(
                    rescan_plex_requests_func(
                        db,
                        runtime_settings,
                        plex,
                        on_event=emit,
                        shallow=shallow,
                    )
                )
                get_task = asyncio.create_task(queue.get())

                while True:
                    done, _pending = await asyncio.wait(
                        {task, get_task}, return_when=asyncio.FIRST_COMPLETED
                    )
                    if get_task in done:
                        payload = get_task.result()
                        if payload is not None:
                            yield serialize_sse(payload)
                        get_task = asyncio.create_task(queue.get())
                        continue

                    if task in done:
                        if not get_task.done():
                            get_task.cancel()
                            with contextlib.suppress(asyncio.CancelledError):
                                await get_task
                        while not queue.empty():
                            payload = queue.get_nowait()
                            if payload is not None:
                                yield serialize_sse(payload)
                        break

                resynced, failed, completed = await task
                yield serialize_sse(
                    build_sse_progress_func(
                        "complete",
                        mode=mode,
                        resynced=resynced,
                        failed=failed,
                        completed=completed,
                        active=[],
                        message=(
                            (
                                "Partial Plex sync completed. "
                                if shallow
                                else "Full Plex sync completed. "
                            )
                            + f"Re-synced {resynced} TV request(s), "
                            f"{failed} failed, "
                            f"{completed} transitioned to completed."
                        ),
                    )
                )
            finally:
                await plex.close()

    except Exception as exc:
        logger.exception("Plex SSE re-scan failed")
        yield serialize_sse({"phase": "error", "message": f"Plex re-scan error: {exc}"})


@dataclass(slots=True)
class PreparedOverseerrImport:
    external_id: str
    media_type: MediaType
    tmdb_id: int | None
    tvdb_id: int | None
    title: str
    year: int | None
    requester_username: str | None
    requester_email: str | None
    overseerr_request_id: int | None
    media_details: dict | None


def extract_title_and_year_from_media_details(
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


async def prepare_overseerr_import(
    ov_req: dict[str, Any],
    overseerr_service,
    semaphore: asyncio.Semaphore,
    media_details_tasks: dict[tuple[str, int], asyncio.Task[dict | None]],
    media_details_lock: asyncio.Lock,
) -> PreparedOverseerrImport | None:
    """Collect per-request metadata before serial DB writes."""
    media = ov_req.get("media") or {}
    tmdb_id = media.get("tmdbId")
    tvdb_id = media.get("tvdbId")
    overseerr_request_id = ov_req.get("id")

    if tmdb_id is None and tvdb_id is None:
        return None

    external_id = str(tmdb_id) if tmdb_id is not None else str(tvdb_id)
    media_type = MediaType.MOVIE if media.get("mediaType", "") == "movie" else MediaType.TV

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
        key = (media_type_for_api, media_external_id)

        async with media_details_lock:
            media_details_task = media_details_tasks.get(key)
            if media_details_task is None:

                async def fetch_media_details() -> dict | None:
                    async with semaphore:
                        return await overseerr_service.get_media_details(
                            media_type_for_api,
                            media_external_id,
                        )

                media_details_task = asyncio.create_task(fetch_media_details())
                media_details_tasks[key] = media_details_task

        media_details = await media_details_task

    title, year = extract_title_and_year_from_media_details(media_details)
    return PreparedOverseerrImport(
        external_id=external_id,
        media_type=media_type,
        tmdb_id=tmdb_id,
        tvdb_id=tvdb_id,
        title=title,
        year=year,
        requester_username=username,
        requester_email=email,
        overseerr_request_id=overseerr_request_id,
        media_details=media_details,
    )


async def import_overseerr_requests(
    db,
    runtime_settings,
    *,
    on_event=None,
    overseerr_service_cls,
    plex_service_cls,
    evaluate_imported_request_func,
    prepare_overseerr_import_func,
    logger,
) -> tuple[int, int]:
    """Fetch, filter, and import new Overseerr requests into the local DB."""
    overseerr_service = overseerr_service_cls(settings=runtime_settings)

    async def emit(payload: dict[str, Any]) -> None:
        if on_event is None:
            return
        result = on_event(payload)
        if asyncio.iscoroutine(result):
            await result

    def progress_current(current: int, total: int) -> int:
        if total <= 0:
            return 0
        return min(current, total - 1)

    try:
        await emit(
            build_sse_progress(
                "fetching",
                current=0,
                total=1,
                active=[],
                message="Fetching requests from Overseerr...",
            )
        )
        overseerr_requests = await overseerr_service.get_all_requests(status=None)
        if not overseerr_requests:
            return 0, 0

        result = await db.execute(
            select(RequestModel.external_id, RequestModel.overseerr_request_id)
        )
        existing_rows = result.fetchall()
        existing_external_ids = {row[0] for row in existing_rows}
        existing_request_ids = {row[1] for row in existing_rows if row[1] is not None}

        actionable_requests = []
        for index, ov_req in enumerate(overseerr_requests, start=1):
            media = ov_req.get("media") or {}
            request_status = overseerr_service.normalize_request_status(ov_req.get("status"))
            media_status = overseerr_service.normalize_media_status(media.get("status"))
            if request_status in {"pending", "approved"} and media_status != "available":
                actionable_requests.append(ov_req)
            await emit(
                build_sse_progress(
                    "filtering",
                    current=progress_current(index, len(overseerr_requests)),
                    total=len(overseerr_requests),
                    active=[],
                    message="Filtering actionable Overseerr requests...",
                )
            )

        sync_concurrency = max(1, runtime_settings.overseerr_sync_concurrency)
        sync_semaphore = asyncio.Semaphore(sync_concurrency)
        media_details_tasks: dict[tuple[str, int], asyncio.Task[dict | None]] = {}
        media_details_lock = asyncio.Lock()
        active_prefetch: list[str] = []
        prefetch_completed = 0
        prefetch_lock = asyncio.Lock()

        async def prepare_with_progress(ov_req: dict[str, Any]) -> PreparedOverseerrImport | None:
            nonlocal prefetch_completed
            media = ov_req.get("media") or {}
            title = media.get("title") or media.get("name") or f"Request #{ov_req.get('id', '?')}"
            async with prefetch_lock:
                active_prefetch.append(title)
                active_snapshot = active_prefetch[:16]
                completed_snapshot = prefetch_completed
            await emit(
                build_sse_progress(
                    "prefetching",
                    current=progress_current(completed_snapshot, len(actionable_requests)),
                    total=len(actionable_requests),
                    title=title,
                    active=active_snapshot,
                    message=f"Fetching metadata for {title}...",
                )
            )
            try:
                return await prepare_overseerr_import_func(
                    ov_req,
                    overseerr_service,
                    sync_semaphore,
                    media_details_tasks,
                    media_details_lock,
                )
            finally:
                async with prefetch_lock:
                    with contextlib.suppress(ValueError):
                        active_prefetch.remove(title)
                    prefetch_completed += 1
                    active_snapshot = active_prefetch[:16]
                    completed_snapshot = prefetch_completed
                await emit(
                    build_sse_progress(
                        "prefetching",
                        current=progress_current(completed_snapshot, len(actionable_requests)),
                        total=len(actionable_requests),
                        title=title,
                        active=active_snapshot,
                        message=f"Fetched metadata for {title}.",
                    )
                )

        prepared_requests = await asyncio.gather(
            *(prepare_with_progress(ov_req) for ov_req in actionable_requests),
            return_exceptions=True,
        )

        synced_count = 0
        skipped_count = 0
        new_tv_requests: list[RequestModel] = []

        for index, prepared_request in enumerate(prepared_requests, start=1):
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
                await emit(
                    build_sse_progress(
                        "importing",
                        current=progress_current(index - 1, len(prepared_requests)),
                        total=len(prepared_requests),
                        title=prepared.title,
                        active=[prepared.title] if prepared.title else [],
                        message=f"Importing {prepared.title or 'request'}...",
                    )
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
                    requester_username=prepared.requester_username,
                    requester_email=prepared.requester_email,
                    status=RequestStatus.PENDING,
                    overseerr_request_id=prepared.overseerr_request_id,
                )
                db.add(new_request)
                await db.flush()
                await evaluate_imported_request_func(
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

        await db.commit()

        if synced_count > 0:
            from app.siftarr.services.episode_sync_service import EpisodeSyncService

            plex_service = plex_service_cls(settings=runtime_settings)
            try:
                episode_sync = EpisodeSyncService(
                    db, overseerr=overseerr_service, plex=plex_service
                )
                for index, req in enumerate(new_tv_requests, start=1):
                    try:
                        await emit(
                            build_sse_progress(
                                "episode_sync",
                                current=progress_current(index - 1, len(new_tv_requests)),
                                total=len(new_tv_requests),
                                title=req.title,
                                active=[req.title],
                                message=f"Syncing TV episodes for {req.title}...",
                            )
                        )
                        await episode_sync.sync_request(req.id)
                    except Exception:
                        logger.exception(
                            "Episode sync failed for request_id=%s during import",
                            req.id,
                        )
                    finally:
                        await emit(
                            build_sse_progress(
                                "episode_sync",
                                current=progress_current(index, len(new_tv_requests)),
                                total=len(new_tv_requests),
                                title=req.title,
                                active=[],
                                message=f"Finished TV episode sync for {req.title}.",
                            )
                        )
            finally:
                await plex_service.close()

        return synced_count, skipped_count
    finally:
        await overseerr_service.close()


async def sync_overseerr_generator(
    *,
    async_session_maker,
    build_effective_settings_func,
    import_overseerr_requests_func,
    build_sse_progress_func,
    logger,
):
    """Yield SSE events for Overseerr sync progress."""
    try:
        yield serialize_sse({"phase": "connecting"})

        async with async_session_maker() as db:
            effective_settings = await build_effective_settings_func(db)
            if not effective_settings.get("overseerr_url") or not effective_settings.get(
                "overseerr_api_key"
            ):
                yield serialize_sse(
                    {
                        "phase": "error",
                        "message": "Overseerr is not configured. Please set URL and API key.",
                    }
                )
                return

            runtime_settings = get_settings()
            queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

            async def emit(payload: dict[str, Any]) -> None:
                await queue.put(payload)

            task = asyncio.create_task(
                import_overseerr_requests_func(db, runtime_settings, on_event=emit)
            )
            get_task = asyncio.create_task(queue.get())

            while True:
                done, _pending = await asyncio.wait(
                    {task, get_task}, return_when=asyncio.FIRST_COMPLETED
                )
                if get_task in done:
                    payload = get_task.result()
                    if payload is not None:
                        yield serialize_sse(payload)
                    get_task = asyncio.create_task(queue.get())
                    continue

                if task in done:
                    if not get_task.done():
                        get_task.cancel()
                        with contextlib.suppress(asyncio.CancelledError):
                            await get_task
                    while not queue.empty():
                        payload = queue.get_nowait()
                        if payload is not None:
                            yield serialize_sse(payload)
                    break

            synced_count, skipped_count = await task
            if synced_count > 0:
                message = f"Synced {synced_count} new request(s) from Overseerr"
            elif synced_count == 0 and skipped_count == 0:
                message = "No requests found in Overseerr"
            else:
                message = (
                    "No new actionable requests to sync "
                    f"({skipped_count} already existed or were already available)"
                )

            yield serialize_sse(
                build_sse_progress_func(
                    "complete",
                    active=[],
                    synced=synced_count,
                    skipped=skipped_count,
                    message=message,
                )
            )
    except Exception as exc:
        logger.exception("Overseerr SSE sync failed")
        yield serialize_sse(
            build_sse_progress_func("error", active=[], message=f"Sync error: {exc}")
        )
