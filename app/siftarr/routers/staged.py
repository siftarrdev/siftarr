"""Staged torrent management router."""

import json
import logging
import os
import re
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Form, HTTPException, Query
from fastapi import Request as FastAPIRequest
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.siftarr.config import get_settings
from app.siftarr.database import get_db
from app.siftarr.models.activity_log import ActivityLog, EventType
from app.siftarr.models.request import (
    MediaType,
    Request,
    RequestStatus,
    is_active_staging_workflow_status,
)
from app.siftarr.models.staged_torrent import StagedTorrent
from app.siftarr.services import staging_decision_log
from app.siftarr.services.admin.plex_polling_service import CheckRequestResult, PlexPollingService
from app.siftarr.services.auth_service import require_session_or_api_key
from app.siftarr.services.integrations.plex_service import PlexService
from app.siftarr.services.integrations.qbittorrent_service import (
    BulkAddResult,
    BulkTorrentPayload,
    MediaCategory,
    QbittorrentService,
)
from app.siftarr.services.lifecycle.activity_log_service import ActivityLogService
from app.siftarr.services.lifecycle.download_queue_service import DownloadQueueService
from app.siftarr.services.lifecycle.lifecycle_service import LifecycleService
from app.siftarr.services.lifecycle.overseerr_sync_service import (
    approve_overseerr_request_best_effort,
)
from app.siftarr.services.search_history_service import SearchHistoryService
from app.siftarr.services.stats_metrics_service import record_staged_release_fact

logger = logging.getLogger(__name__)

_BTIH_RE = re.compile(r"urn:btih:([0-9a-fA-F]{40}|[2-7A-Za-z]{32})", re.IGNORECASE)


def _staged_download_url(torrent: StagedTorrent) -> str | None:
    """Return stored release download URL from staging sidecar metadata."""
    try:
        with open(torrent.json_path) as f:
            metadata = json.load(f)
    except OSError, json.JSONDecodeError, TypeError:
        return None
    release = metadata.get("release") if isinstance(metadata, dict) else None
    download_url = release.get("download_url") if isinstance(release, dict) else None
    return download_url if isinstance(download_url, str) and download_url else None


def _staged_sidecar_magnet_url(torrent: StagedTorrent) -> str | None:
    """Return stored release magnet URL from staging sidecar metadata."""
    try:
        with open(torrent.json_path) as f:
            metadata = json.load(f)
    except OSError, json.JSONDecodeError, TypeError:
        return None
    release = metadata.get("release") if isinstance(metadata, dict) else None
    magnet_url = release.get("magnet_url") if isinstance(release, dict) else None
    return magnet_url if isinstance(magnet_url, str) and magnet_url else None


def _torrent_submission_source(torrent: StagedTorrent) -> tuple[str | None, str | None, str | None]:
    """Return (source_type, value, error) for qBit submission."""
    torrent_path = getattr(torrent, "torrent_path", None)
    if isinstance(torrent_path, str) and torrent_path and os.path.exists(torrent_path):
        return "torrent_path", torrent_path, None

    magnet_url = getattr(torrent, "magnet_url", None)
    if isinstance(magnet_url, str) and magnet_url:
        return "magnet_uri", magnet_url, None

    sidecar_magnet_url = _staged_sidecar_magnet_url(torrent)
    if sidecar_magnet_url:
        return "magnet_uri", sidecar_magnet_url, None

    download_url = _staged_download_url(torrent)
    if download_url:
        return "magnet_uri", download_url, None

    return None, None, "No local torrent file, magnet URL, or sidecar download URL available"


def _torrent_known_hash(torrent: StagedTorrent) -> str | None:
    info_hash = getattr(torrent, "info_hash", None)
    if isinstance(info_hash, str) and info_hash:
        return info_hash.lower()
    magnet_url = getattr(torrent, "magnet_url", None)
    if isinstance(magnet_url, str):
        match = _BTIH_RE.search(magnet_url)
        if match:
            return match.group(1).lower()
    return None


def _is_hash_like(value: str | None) -> bool:
    return isinstance(value, str) and bool(re.fullmatch(r"[0-9a-fA-F]{40}", value))


async def _confirm_existing_torrent(
    qbittorrent: QbittorrentService,
    torrent: StagedTorrent,
) -> str | None:
    """Return an existing qBit hash for an already-added torrent, if verifiable."""
    known_hash = _torrent_known_hash(torrent)
    if known_hash:
        info = await qbittorrent.get_torrent_info(known_hash)
        if info:
            found_hash = info.get("hash")
            return found_hash if _is_hash_like(found_hash) else known_hash

    info = await qbittorrent.get_torrent_info_by_name(torrent.title)
    if info:
        found_hash = info.get("hash")
        if isinstance(found_hash, str) and _is_hash_like(found_hash):
            return found_hash.lower()
    return None


def _delete_staging_files(paths: list[tuple[str, str]]) -> None:
    for torrent_path, json_path in paths:
        try:
            if os.path.exists(torrent_path):
                os.remove(torrent_path)
            if os.path.exists(json_path):
                os.remove(json_path)
        except OSError:
            pass


STAGING_DECISION_LOG_PATH = staging_decision_log.STAGING_DECISION_LOG_PATH

router = APIRouter(prefix="/staged", tags=["staged"])


def _parse_filter_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid ISO datetime: {value}") from exc
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=datetime.now().astimezone().tzinfo)


def _entry_text_contains(entry: dict[str, Any], needle: str) -> bool:
    haystacks = [entry.get("request", {}) if isinstance(entry.get("request"), dict) else {}]
    haystacks.extend(c for c in entry.get("all_candidates", []) if isinstance(c, dict))
    haystacks.extend(c for c in entry.get("top_candidates", []) if isinstance(c, dict))
    selected = entry.get("selected_release")
    if isinstance(selected, dict):
        haystacks.append(selected)
    return any(needle in str(h.get("title") or "").casefold() for h in haystacks)


def _entry_has_rule(entry: dict[str, Any], rule_name: str) -> bool:
    target = rule_name.casefold()
    candidates = list(entry.get("all_candidates", [])) + list(entry.get("top_candidates", []))
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        for match in candidate.get("matches", []) or candidate.get("rule_matches", []) or []:
            if isinstance(match, dict) and target in str(match.get("rule_name") or "").casefold():
                return True
    return False


@router.get("/decision-log", dependencies=[Depends(require_session_or_api_key)])
async def get_decision_log(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    request_id: int | None = None,
    media_type: str | None = None,
    event_type: str | None = None,
    selection_source: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    title: str | None = None,
    rule_name: str | None = None,
    outcome: str | None = None,
) -> dict[str, Any]:
    """Return normalized staging decision log entries for authenticated sessions or API keys."""
    start_date = _parse_filter_date(date_from)
    end_date = _parse_filter_date(date_to)
    filters = {
        "request_id": request_id,
        "media_type": media_type,
        "event_type": event_type,
        "selection_source": selection_source,
        "date_from": date_from,
        "date_to": date_to,
        "title": title,
        "rule_name": rule_name,
        "outcome": outcome,
    }
    entries = staging_decision_log.read_entries(STAGING_DECISION_LOG_PATH)
    filtered: list[dict[str, Any]] = []
    for entry in entries:
        raw_request = entry.get("request")
        request_payload: dict[str, Any] = raw_request if isinstance(raw_request, dict) else {}
        raw_selection = entry.get("selection")
        selection: dict[str, Any] = raw_selection if isinstance(raw_selection, dict) else {}
        logged_at = staging_decision_log._parse_dt(entry.get("logged_at"))  # noqa: SLF001
        if request_id is not None and request_payload.get("id") != request_id:
            continue
        if media_type and request_payload.get("media_type") != media_type:
            continue
        if event_type and entry.get("event_type") != event_type:
            continue
        if outcome and entry.get("outcome") != outcome:
            continue
        if (
            selection_source
            and selection.get("selection_source") != selection_source
            and selection.get("source") != selection_source
        ):
            continue
        if start_date and (logged_at is None or logged_at < start_date):
            continue
        if end_date and (logged_at is None or logged_at > end_date):
            continue
        if title and not _entry_text_contains(entry, title.casefold()):
            continue
        if rule_name and not _entry_has_rule(entry, rule_name):
            continue
        filtered.append(entry)
    filtered.sort(key=staging_decision_log.entry_sort_key, reverse=True)
    total = len(filtered)
    start = (page - 1) * page_size
    items = filtered[start : start + page_size]
    return {
        "items": items,
        "page": page,
        "page_size": page_size,
        "total": total,
        "has_next": start + page_size < total,
        "filters": {k: v for k, v in filters.items() if v is not None},
    }


@router.get("/{torrent_id}/alternatives", dependencies=[Depends(require_session_or_api_key)])
async def get_staged_alternatives(
    torrent_id: int,
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    payload = await SearchHistoryService(db).staged_alternatives(torrent_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="Staged torrent not found")
    return JSONResponse(payload)


def _safe_local_redirect_url(redirect_to: str | None, default: str) -> str:
    """Return redirect_to only when it is a same-origin absolute path."""
    if not redirect_to or "\\" in redirect_to or not redirect_to.startswith("/"):
        return default
    if redirect_to.startswith("//"):
        return default

    parsed = urlsplit(redirect_to)
    if parsed.scheme or parsed.netloc:
        return default
    return redirect_to


def _build_torrent_payload(torrent: StagedTorrent | None) -> dict[str, Any] | None:
    """Convert a staged torrent into a compact serializable payload."""
    if torrent is None:
        return None

    return {
        "id": torrent.id,
        "title": torrent.title,
        "score": torrent.score,
        "size": torrent.size,
        "indexer": torrent.indexer,
        "status": torrent.status,
        "selection_source": torrent.selection_source,
    }


def log_staging_decision(
    *,
    request: Request | None,
    approved_torrent: StagedTorrent,
    rules_selected_torrent: StagedTorrent | None,
) -> None:
    """Append a final staging approval decision for later rule tuning."""
    original_path = staging_decision_log.STAGING_DECISION_LOG_PATH
    staging_decision_log.STAGING_DECISION_LOG_PATH = STAGING_DECISION_LOG_PATH
    try:
        staging_decision_log.log_staging_decision(
            request=request,
            approved_torrent=approved_torrent,
            rules_selected_torrent=rules_selected_torrent,
        )
    finally:
        staging_decision_log.STAGING_DECISION_LOG_PATH = original_path


def log_replacement_decision(
    *,
    request: Request | None,
    new_torrent: StagedTorrent,
    replaced_torrent: StagedTorrent,
    reason: str | None = None,
) -> None:
    """Append a replacement decision when an approved torrent is replaced."""
    original_path = staging_decision_log.STAGING_DECISION_LOG_PATH
    staging_decision_log.STAGING_DECISION_LOG_PATH = STAGING_DECISION_LOG_PATH
    try:
        staging_decision_log.log_replacement_decision(
            request=request,
            new_torrent=new_torrent,
            replaced_torrent=replaced_torrent,
            reason=reason,
        )
    finally:
        staging_decision_log.STAGING_DECISION_LOG_PATH = original_path


def log_manual_discard_decision(
    *,
    request: Request | None,
    rejected_torrent: StagedTorrent,
    reason: str = "Manually discarded",
) -> None:
    """Append a manual discard/rejection decision for later rule tuning."""
    original_path = staging_decision_log.STAGING_DECISION_LOG_PATH
    staging_decision_log.STAGING_DECISION_LOG_PATH = STAGING_DECISION_LOG_PATH
    try:
        staging_decision_log.log_manual_discard_decision(
            request=request,
            rejected_torrent=rejected_torrent,
            reason=reason,
        )
    finally:
        staging_decision_log.STAGING_DECISION_LOG_PATH = original_path


def _wants_json(http_request: FastAPIRequest) -> bool:
    return "application/json" in http_request.headers.get("accept", "")


def _progress_percent(progress: float | None) -> float | None:
    if progress is None:
        return None
    return round(progress * 100, 1)


def _download_completed_torrent_ids(details: str | None) -> set[int]:
    if not details:
        return set()
    try:
        payload = json.loads(details)
    except TypeError, ValueError:
        return set()
    if not isinstance(payload, dict):
        return set()

    torrent_ids: set[int] = set()
    torrent_id = payload.get("torrent_id")
    if isinstance(torrent_id, int):
        torrent_ids.add(torrent_id)
    for item in payload.get("done_torrents") or []:
        if isinstance(item, dict) and isinstance(item.get("torrent_id"), int):
            torrent_ids.add(item["torrent_id"])
    return torrent_ids


async def _finalize_action_response(
    http_request: FastAPIRequest,
    message: str,
    *,
    redirect_url: str = "/?tab=staged",
    payload: dict[str, Any] | None = None,
):
    if _wants_json(http_request):
        return JSONResponse({"status": "ok", "message": message, **(payload or {})})
    return RedirectResponse(url=redirect_url, status_code=303)


async def _approve_torrent(
    torrent: StagedTorrent,
    db: AsyncSession,
    *,
    commit_transition: bool = True,
    cleanup_paths: list[tuple[str, str]] | None = None,
) -> bool:
    request = None
    if torrent.request_id:
        result = await db.execute(select(Request).where(Request.id == torrent.request_id))
        request = result.scalar_one_or_none()

    category = (
        MediaCategory.MOVIES
        if request and request.media_type == MediaType.MOVIE
        else MediaCategory.TV
    )

    rules_selected_torrent = None
    if request is not None:
        rules_selected_result = await db.execute(
            select(StagedTorrent)
            .where(
                StagedTorrent.request_id == request.id,
                StagedTorrent.selection_source == "rule",
                StagedTorrent.status.in_(["staged", "approved"]),
            )
            .order_by(StagedTorrent.score.desc(), StagedTorrent.created_at.asc())
        )
        rules_selected_torrent = rules_selected_result.scalars().first()

    runtime_settings = get_settings()
    qbittorrent = QbittorrentService(settings=runtime_settings)

    # Add to qBittorrent (idempotent — if already present by info hash it
    # returns the existing hash and skips the add).
    source_type, source_value, source_error = _torrent_submission_source(torrent)
    if source_error or source_value is None:
        logger.warning(
            "Cannot approve staged torrent_id=%s title=%r: %s",
            torrent.id,
            torrent.title,
            source_error,
        )
        torrent_hash = await _confirm_existing_torrent(qbittorrent, torrent)
        if torrent_hash is None:
            return False
    else:
        torrent_hash: str | None = None
        if source_type == "magnet_uri":
            torrent_hash = await qbittorrent.add_torrent(magnet_uri=source_value, category=category)
        else:
            torrent_hash = await qbittorrent.add_torrent(
                torrent_path=source_value, category=category
            )

    if torrent_hash is None:
        torrent_hash = await _confirm_existing_torrent(qbittorrent, torrent)
        if torrent_hash is None:
            return False

    try:
        await approve_overseerr_request_best_effort(db, request, reason="staged_approval_qbit_sent")
    except Exception:
        logger.exception(
            "Best-effort Overseerr approval failed for request_id=%s", torrent.request_id
        )

    activity_log = ActivityLogService(db)
    await activity_log.log(
        EventType.RELEASE_APPROVED,
        request_id=torrent.request_id,
        details={"torrent_id": torrent.id, "title": torrent.title},
    )
    await record_staged_release_fact(db, torrent)
    await activity_log.log(
        EventType.DOWNLOAD_STARTED,
        request_id=torrent.request_id,
        details={"torrent_id": torrent.id, "title": torrent.title},
    )

    log_staging_decision(
        request=request,
        approved_torrent=torrent,
        rules_selected_torrent=rules_selected_torrent,
    )

    # Snapshot paths for deletion by caller after commit succeeds.
    torrent_path = torrent.torrent_path
    json_path = torrent.json_path

    torrent.status = "approved"
    if isinstance(torrent_hash, str) and _is_hash_like(torrent_hash):
        torrent.info_hash = torrent_hash.lower()
    if request:
        lifecycle_service = LifecycleService(db)
        if request.status not in (
            RequestStatus.COMPLETED,
            RequestStatus.FAILED,
            RequestStatus.DENIED,
        ):
            if commit_transition:
                await lifecycle_service.transition(request.id, RequestStatus.DOWNLOADING)
            else:
                await lifecycle_service.transition(
                    request.id,
                    RequestStatus.DOWNLOADING,
                    commit=False,
                )

    if cleanup_paths is not None:
        cleanup_paths.append((torrent_path, json_path))

    return True


async def _load_approval_context(
    torrent: StagedTorrent,
    db: AsyncSession,
) -> tuple[Request | None, StagedTorrent | None, MediaCategory]:
    request = None
    if torrent.request_id:
        result = await db.execute(select(Request).where(Request.id == torrent.request_id))
        request = result.scalar_one_or_none()

    category = (
        MediaCategory.MOVIES
        if request and request.media_type == MediaType.MOVIE
        else MediaCategory.TV
    )
    rules_selected_torrent = None
    if request is not None:
        rules_selected_result = await db.execute(
            select(StagedTorrent)
            .where(
                StagedTorrent.request_id == request.id,
                StagedTorrent.selection_source == "rule",
                StagedTorrent.status.in_(["staged", "approved"]),
            )
            .order_by(StagedTorrent.score.desc(), StagedTorrent.created_at.asc())
        )
        rules_selected_torrent = rules_selected_result.scalars().first()
    return request, rules_selected_torrent, category


def _bulk_payload_for_torrent(
    torrent: StagedTorrent,
    category: MediaCategory,
) -> tuple[BulkTorrentPayload | None, str | None]:
    source_type, source_value, source_error = _torrent_submission_source(torrent)
    if source_error or source_value is None:
        return None, source_error
    if source_type == "magnet_uri":
        return BulkTorrentPayload(
            key=torrent.id,
            title=torrent.title,
            magnet_uri=source_value,
            category=category,
        ), None
    return BulkTorrentPayload(
        key=torrent.id,
        title=torrent.title,
        torrent_path=source_value,
        category=category,
    ), None


async def _mark_torrent_approved_after_qbit(
    torrent: StagedTorrent,
    db: AsyncSession,
    *,
    request: Request | None,
    rules_selected_torrent: StagedTorrent | None,
    torrent_hash: str | None,
    cleanup_paths: list[tuple[str, str]],
) -> None:
    try:
        await approve_overseerr_request_best_effort(db, request, reason="staged_approval_qbit_sent")
    except Exception:
        logger.exception(
            "Best-effort Overseerr approval failed for request_id=%s", torrent.request_id
        )

    activity_log = ActivityLogService(db)
    await activity_log.log(
        EventType.RELEASE_APPROVED,
        request_id=torrent.request_id,
        details={"torrent_id": torrent.id, "title": torrent.title},
    )
    await record_staged_release_fact(db, torrent)
    await activity_log.log(
        EventType.DOWNLOAD_STARTED,
        request_id=torrent.request_id,
        details={"torrent_id": torrent.id, "title": torrent.title},
    )

    log_staging_decision(
        request=request,
        approved_torrent=torrent,
        rules_selected_torrent=rules_selected_torrent,
    )

    cleanup_paths.append((torrent.torrent_path, torrent.json_path))
    torrent.status = "approved"
    if isinstance(torrent_hash, str) and _is_hash_like(torrent_hash):
        torrent.info_hash = torrent_hash.lower()
    if request and request.status not in (
        RequestStatus.COMPLETED,
        RequestStatus.FAILED,
        RequestStatus.DENIED,
    ):
        lifecycle_service = LifecycleService(db)
        await lifecycle_service.transition(request.id, RequestStatus.DOWNLOADING, commit=False)


async def _discard_torrent(torrent: StagedTorrent, db: AsyncSession) -> bool:
    # Snapshot paths before any commit that might expire the torrent object
    torrent_path = torrent.torrent_path
    json_path = torrent.json_path
    request: Request | None = None

    if torrent.request_id:
        result = await db.execute(select(Request).where(Request.id == torrent.request_id))
        request = result.scalar_one_or_none()
        if request:
            if request.status == RequestStatus.DOWNLOADING:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "Cannot discard a torrent that is already downloading. Use Replace instead to select a different torrent."
                    ),
                )
            if request.status == RequestStatus.STAGED:
                lifecycle_service = LifecycleService(db)
                await lifecycle_service.transition(torrent.request_id, RequestStatus.PENDING)

    torrent.status = "discarded"
    log_manual_discard_decision(request=request, rejected_torrent=torrent)

    try:
        if os.path.exists(torrent_path):
            os.remove(torrent_path)
        if os.path.exists(json_path):
            os.remove(json_path)
    except OSError:
        pass

    return True


async def _reconcile_request_via_plex(
    db: AsyncSession,
    *,
    request_id: int,
    title: str,
    runtime_settings,
) -> CheckRequestResult:
    plex = PlexService(settings=runtime_settings)
    plex_polling = PlexPollingService(db, plex)
    reconcile_result = await plex_polling.check_request(request_id)

    if reconcile_result.available:
        activity_log = ActivityLogService(db)
        await activity_log.log(
            EventType.PLEX_AVAILABLE,
            request_id=request_id,
            details={"title": title, "reason": reconcile_result.reason},
        )
        await db.commit()

    return reconcile_result


async def _resolve_download_completion(
    db: AsyncSession,
    *,
    torrent: StagedTorrent,
    qbit_done: bool,
    runtime_settings,
) -> tuple[bool, str | None]:
    if not qbit_done or not torrent.request_id:
        return False, None

    reconcile_result = await _reconcile_request_via_plex(
        db,
        request_id=torrent.request_id,
        title=torrent.title,
        runtime_settings=runtime_settings,
    )

    status_after = reconcile_result.status_after
    if isinstance(status_after, RequestStatus):
        return reconcile_result.available, status_after.value
    if status_after is not None:
        return reconcile_result.available, str(status_after)
    return reconcile_result.available, None


def _should_refresh_staged_tab(
    *,
    qbit_complete: bool,
    plex_available: bool,
    request_status: str,
    resolved_request_status: str,
) -> bool:
    """Return whether the staged tab should immediately refresh server-rendered state."""
    return plex_available or resolved_request_status != request_status


@router.post("/{torrent_id}/approve", response_model=None)
async def approve_staged_torrent(
    torrent_id: int,
    http_request: FastAPIRequest,
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse | JSONResponse:
    """Approve a staged torrent - send to qBittorrent."""
    result = await db.execute(select(StagedTorrent).where(StagedTorrent.id == torrent_id))
    torrent = result.scalar_one_or_none()

    if not torrent:
        raise HTTPException(status_code=404, detail="Staged torrent not found")

    cleanup_paths: list[tuple[str, str]] = []
    success = await _approve_torrent(
        torrent,
        db,
        commit_transition=False,
        cleanup_paths=cleanup_paths,
    )
    if not success:
        await db.rollback()
        raise HTTPException(status_code=500, detail="Failed to approve staged torrent")

    await db.commit()
    if success:
        _delete_staging_files(cleanup_paths)

    return await _finalize_action_response(
        http_request,
        "Torrent approved successfully",
        redirect_url="/?tab=staged",
        payload={
            "torrent_id": torrent.id,
            "torrent_status": "approved",
            "request_status": RequestStatus.DOWNLOADING.value,
            "refresh": ["staged", "downloading"],
        },
    )


@router.post("/{torrent_id}/discard", response_model=None)
async def discard_staged_torrent(
    torrent_id: int,
    http_request: FastAPIRequest,
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse | JSONResponse:
    """Discard a staged torrent - delete files."""
    result = await db.execute(select(StagedTorrent).where(StagedTorrent.id == torrent_id))
    torrent = result.scalar_one_or_none()

    if not torrent:
        raise HTTPException(status_code=404, detail="Staged torrent not found")

    await _discard_torrent(torrent, db)
    await db.commit()

    return await _finalize_action_response(
        http_request,
        "Torrent discarded successfully",
        redirect_url="/?tab=staged",
    )


@router.post("/{torrent_id}/delete-download", response_model=None)
async def delete_downloading_torrent(
    torrent_id: int,
    http_request: FastAPIRequest,
    redirect_to: str | None = Form(None),
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse | JSONResponse:
    """Delete an approved torrent and its qBittorrent data, then reset pending state."""
    result = await db.execute(select(StagedTorrent).where(StagedTorrent.id == torrent_id))
    torrent = result.scalar_one_or_none()
    if not torrent:
        raise HTTPException(status_code=404, detail="Staged torrent not found")
    if torrent.status != "approved":
        raise HTTPException(status_code=400, detail="Only downloading torrents can be deleted")

    service = DownloadQueueService(db, QbittorrentService(settings=get_settings()))
    delete_result = await service.delete_download(torrent)
    if not delete_result.success:
        await db.rollback()
        raise HTTPException(
            status_code=502, detail=delete_result.message or "qBittorrent delete failed"
        )

    await db.commit()
    return await _finalize_action_response(
        http_request,
        "Download deleted; request returned to pending.",
        redirect_url=_safe_local_redirect_url(redirect_to, "/?tab=downloading"),
        payload={
            "torrent_id": torrent.id,
            "torrent_status": "discarded",
            "refresh": ["downloading", "staged"],
        },
    )


@router.post("/bulk", response_model=None)
async def bulk_staged_action(
    http_request: FastAPIRequest,
    action: str = Form(...),
    torrent_ids: list[int] = Form(default=[]),
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse | JSONResponse:
    """Apply an approve/discard action to multiple staged torrents."""
    if not torrent_ids:
        return await _finalize_action_response(
            http_request,
            "No staged torrents were selected.",
            redirect_url="/?tab=staged",
        )

    result = await db.execute(select(StagedTorrent).where(StagedTorrent.id.in_(torrent_ids)))
    torrents = list(result.scalars().all())

    if action not in {"approve", "discard"}:
        raise HTTPException(status_code=400, detail="Invalid bulk action")

    processed = 0
    failed: list[dict[str, Any]] = []
    cleanup_paths: list[tuple[str, str]] = []
    if action == "approve":
        runtime_settings = get_settings()
        qbittorrent = QbittorrentService(settings=runtime_settings)
        contexts: dict[int, tuple[Request | None, StagedTorrent | None]] = {}
        payloads: list[BulkTorrentPayload] = []
        for torrent in torrents:
            request, rules_selected_torrent, category = await _load_approval_context(torrent, db)
            contexts[torrent.id] = (request, rules_selected_torrent)
            payload, source_error = _bulk_payload_for_torrent(torrent, category)
            if payload is None:
                logger.warning(
                    "Skipping staged bulk approve torrent_id=%s title=%r: %s",
                    torrent.id,
                    torrent.title,
                    source_error,
                )
                failed_item = {"id": torrent.id, "title": torrent.title}
                if source_error:
                    failed_item["error"] = source_error
                failed.append(failed_item)
                continue
            payloads.append(payload)

        bulk_results = await qbittorrent.add_torrents_bulk(payloads) if payloads else []
        if not isinstance(bulk_results, list):
            # Compatibility for older unit mocks; production service always returns a list.
            bulk_results = []
            for payload in payloads:
                torrent = next(item for item in torrents if item.id == payload.key)
                torrent_hash = await qbittorrent.add_torrent(
                    torrent_path=payload.torrent_path,
                    magnet_uri=payload.magnet_uri,
                    category=payload.category,
                )
                bulk_results.append(
                    BulkAddResult(
                        key=payload.key,
                        success=torrent_hash is not None,
                        torrent_hash=torrent_hash,
                    )
                )

        results_by_id = {result.key: result for result in bulk_results}
        for torrent in torrents:
            result = results_by_id.get(torrent.id)
            if result is None or not result.success:
                if any(item["id"] == torrent.id for item in failed):
                    continue
                error = result.error if result else "Torrent was not submitted"
                logger.warning(
                    "Staged bulk approve failed torrent_id=%s title=%r: %s",
                    torrent.id,
                    torrent.title,
                    error,
                )
                failed_item = {"id": torrent.id, "title": torrent.title}
                if error:
                    failed_item["error"] = error
                failed.append(failed_item)
                continue
            request, rules_selected_torrent = contexts[torrent.id]
            await _mark_torrent_approved_after_qbit(
                torrent,
                db,
                request=request,
                rules_selected_torrent=rules_selected_torrent,
                torrent_hash=result.torrent_hash,
                cleanup_paths=cleanup_paths,
            )
            processed += 1
    else:
        for torrent in torrents:
            success = await _discard_torrent(torrent, db)
            if success:
                processed += 1
            else:
                failed.append({"id": torrent.id, "title": torrent.title})

    await db.commit()
    if action == "approve":
        _delete_staging_files(cleanup_paths)
    action_label = "Approved" if action == "approve" else "Discarded"
    message = f"{action_label} {processed} staged torrent(s)."
    if failed:
        message = f"{message} Failed {len(failed)}: " + ", ".join(
            f"#{item['id']} {item['title']}" for item in failed
        )
    if _wants_json(http_request):
        return JSONResponse(
            {
                "status": "partial" if failed else "ok",
                "message": message,
                "processed": processed,
                "failed": failed,
                "refresh": ["staged", "downloading"] if action == "approve" else ["staged"],
            }
        )
    return await _finalize_action_response(
        http_request,
        message,
        redirect_url="/?tab=staged",
    )


@router.post("/{torrent_id}/replace")
async def replace_staged_torrent(
    torrent_id: int,
    reason: str | None = Form(None),
    redirect_to: str | None = Form(None),
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    """Replace an approved torrent with a new staged one."""
    # Get the new torrent (the one being approved)
    result = await db.execute(select(StagedTorrent).where(StagedTorrent.id == torrent_id))
    new_torrent = result.scalar_one_or_none()

    if not new_torrent:
        raise HTTPException(status_code=404, detail="Staged torrent not found")

    if new_torrent.status != "staged":
        raise HTTPException(
            status_code=400,
            detail="Replacement torrent must be staged",
        )

    # Handle case where torrent has no request_id (manual add)
    if not new_torrent.request_id:
        raise HTTPException(
            status_code=400,
            detail="Cannot replace torrent without an associated request",
        )

    # Find the request associated with this torrent
    result = await db.execute(select(Request).where(Request.id == new_torrent.request_id))
    request = result.scalar_one_or_none()

    if not request:
        raise HTTPException(status_code=404, detail="Associated request not found")

    # Find the currently approved torrent for this request (the one being replaced)
    result = await db.execute(
        select(StagedTorrent).where(
            StagedTorrent.request_id == request.id,
            StagedTorrent.status == "approved",
        )
    )
    old_torrent = result.scalar_one_or_none()

    if not old_torrent:
        raise HTTPException(
            status_code=400,
            detail="No approved torrent found to replace for this request",
        )

    # Determine category
    category = MediaCategory.TV
    if request.media_type == MediaType.MOVIE:
        category = MediaCategory.MOVIES

    # Add new torrent to qBittorrent
    runtime_settings = get_settings()
    qbittorrent = QbittorrentService(settings=runtime_settings)
    success = False

    download_url = _staged_download_url(new_torrent)
    if new_torrent.magnet_url:
        torrent_hash = await qbittorrent.add_torrent(
            magnet_uri=new_torrent.magnet_url,
            category=category,
        )
        success = torrent_hash is not None
    elif download_url and not os.path.exists(new_torrent.torrent_path):
        success = (
            await qbittorrent.add_torrent(magnet_uri=download_url, category=category) is not None
        )
    else:
        success = (
            await qbittorrent.add_torrent(
                torrent_path=new_torrent.torrent_path,
                category=category,
            )
            is not None
        )

    if success:
        # Log the replacement decision
        log_replacement_decision(
            request=request,
            new_torrent=new_torrent,
            replaced_torrent=old_torrent,
            reason=reason,
        )

        # Mark the old torrent as replaced
        old_torrent.status = "replaced"
        old_torrent.replaced_by_id = new_torrent.id
        old_torrent.replaced_at = datetime.now(UTC)
        old_torrent.replacement_reason = reason

        # Mark the new torrent as approved
        new_torrent.status = "approved"
        await record_staged_release_fact(db, new_torrent)

        # Delete staging files for the new torrent
        try:
            if os.path.exists(new_torrent.torrent_path):
                os.remove(new_torrent.torrent_path)
            if os.path.exists(new_torrent.json_path):
                os.remove(new_torrent.json_path)
        except OSError:
            pass

    await db.commit()

    redirect_url = _safe_local_redirect_url(redirect_to, "/?tab=staged")
    return RedirectResponse(url=redirect_url, status_code=303)


@router.get("/download-status")
async def get_download_status(
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Return qBittorrent progress for all approved torrents."""
    result = await db.execute(select(StagedTorrent).where(StagedTorrent.status == "approved"))
    torrents = list(result.scalars().all())

    if not torrents:
        return JSONResponse({"torrents": []})

    # Collect request IDs for status lookup
    request_ids = {t.request_id for t in torrents if t.request_id is not None}
    request_statuses: dict[int, RequestStatus] = {}
    if request_ids:
        req_result = await db.execute(
            select(Request.id, Request.status).where(Request.id.in_(request_ids))
        )
        for req_id, req_status in req_result.all():
            request_statuses[req_id] = req_status

    torrents = [
        torrent
        for torrent in torrents
        if torrent.request_id is None
        or is_active_staging_workflow_status(request_statuses.get(torrent.request_id))
    ]

    if not torrents:
        return JSONResponse({"torrents": []})

    waiting_plex_torrent_ids: set[int] = set()
    legacy_waiting_plex_request_ids: set[int] = set()
    downloading_request_ids = {
        request_id
        for request_id, status in request_statuses.items()
        if status == RequestStatus.DOWNLOADING
    }
    if downloading_request_ids:
        logs_result = await db.execute(
            select(ActivityLog.request_id, ActivityLog.details).where(
                ActivityLog.request_id.in_(downloading_request_ids),
                ActivityLog.event_type == EventType.DOWNLOAD_COMPLETED.value,
            )
        )
        for request_id, details in logs_result.all():
            if request_id is None:
                continue
            torrent_ids = _download_completed_torrent_ids(details)
            if torrent_ids:
                waiting_plex_torrent_ids.update(torrent_ids)
            else:
                legacy_waiting_plex_request_ids.add(request_id)

    active_torrent_ids = {torrent.id for torrent in torrents}
    waiting_plex_torrent_ids &= active_torrent_ids

    runtime_settings = get_settings()
    qbittorrent = QbittorrentService(settings=runtime_settings)

    torrent_data = []
    for torrent in torrents:
        info: dict[str, Any] | None = None
        qbit_progress: float | None = None
        qbit_state: str | None = None

        # Try to get progress via stored info_hash first, then magnet URL,
        # then fall back to name matching
        torrent_hash: str | None = _torrent_known_hash(torrent)
        if not torrent_hash and torrent.magnet_url:
            m = _BTIH_RE.search(torrent.magnet_url)
            if m:
                torrent_hash = m.group(1).lower()

        if torrent_hash:
            info = await qbittorrent.get_torrent_info(torrent_hash)
            if info:
                qbit_progress = info.get("progress")
                qbit_state = info.get("state")
        else:
            info = await qbittorrent.get_torrent_info_by_name(torrent.title)
            if info:
                qbit_progress = info.get("progress")
                qbit_state = info.get("state")

        request_status_value = request_statuses.get(torrent.request_id or -1)
        if isinstance(request_status_value, RequestStatus):
            request_status = request_status_value.value
        elif request_status_value is not None:
            request_status = str(request_status_value)
        else:
            request_status = "unknown"

        # Only treat qBittorrent as "done" when we can confirm progress >= 1.0.
        # Do NOT treat "not found" (None) as done — name matching is unreliable
        # and would falsely mark a downloading torrent as complete.
        qbit_complete = qbit_progress is not None and qbit_progress >= 1.0
        # waiting_for_plex is set when the download_completion service logged a
        # DOWNLOAD_COMPLETED event, OR when we have confirmed (via a non-None
        # progress) that the torrent is done in qBittorrent.
        waiting_for_plex = (
            torrent.id in waiting_plex_torrent_ids
            or (
                torrent.request_id in legacy_waiting_plex_request_ids
                and not (qbit_progress is not None and qbit_progress < 1.0)
            )
            or (qbit_complete and request_status == RequestStatus.DOWNLOADING.value)
        )
        plex_available = False
        resolved_request_status = request_status

        torrent_data.append(
            {
                "id": torrent.id,
                "title": torrent.title,
                "request_id": torrent.request_id,
                "request_status": resolved_request_status,
                "qbit_progress": qbit_progress,
                "qbit_progress_percent": _progress_percent(qbit_progress),
                "qbit_state": qbit_state,
                "qbit_eta_seconds": info.get("eta") if info else None,
                "qbit_download_speed": info.get("dlspeed") if info else None,
                "qbit_complete": qbit_complete,
                "waiting_for_plex": waiting_for_plex,
                "plex_available": plex_available,
                "move_status": torrent.move_status
                if isinstance(torrent.move_status, str)
                else None,
                "moved_path": torrent.moved_path if isinstance(torrent.moved_path, str) else None,
                "move_error": torrent.move_error if isinstance(torrent.move_error, str) else None,
                "refresh_staged_tab": _should_refresh_staged_tab(
                    qbit_complete=qbit_complete,
                    plex_available=plex_available,
                    request_status=request_status,
                    resolved_request_status=resolved_request_status,
                ),
            }
        )

    return JSONResponse({"torrents": torrent_data})


@router.post("/{torrent_id}/check-now")
async def check_now(
    torrent_id: int,
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Manually check download + Plex status for a single torrent."""
    result = await db.execute(select(StagedTorrent).where(StagedTorrent.id == torrent_id))
    torrent = result.scalar_one_or_none()

    if not torrent:
        raise HTTPException(status_code=404, detail="Staged torrent not found")

    runtime_settings = get_settings()
    qbittorrent = QbittorrentService(settings=runtime_settings)

    qbit_progress: float | None = None
    qbit_state: str | None = None

    torrent_hash: str | None = _torrent_known_hash(torrent)
    if not torrent_hash and torrent.magnet_url:
        m = _BTIH_RE.search(torrent.magnet_url)
        if m:
            torrent_hash = m.group(1).lower()

    if torrent_hash:
        info = await qbittorrent.get_torrent_info(torrent_hash)
        if info:
            qbit_progress = info["progress"]
            qbit_state = info["state"]
    else:
        qbit_progress = await qbittorrent.get_torrent_progress_by_name(torrent.title)

    qbit_complete = qbit_progress is not None and qbit_progress >= 1.0
    plex_available = False
    if torrent.request_id:
        try:
            plex_available, _ = await _resolve_download_completion(
                db,
                torrent=torrent,
                qbit_done=qbit_complete,
                runtime_settings=runtime_settings,
            )
        except Exception:
            logger.exception("check-now: Plex check failed for torrent_id=%s", torrent_id)

    await db.commit()

    return JSONResponse(
        {
            "qbit_progress": qbit_progress,
            "qbit_state": qbit_state,
            "qbit_complete": qbit_complete,
            "plex_available": plex_available,
        }
    )
