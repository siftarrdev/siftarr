"""Append-only logging for staging approval decisions."""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.siftarr.models.request import Request
from app.siftarr.models.staged_torrent import StagedTorrent

STAGING_DECISION_LOG_PATH = Path("/data/staging/decision-log.jsonl")


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
    STAGING_DECISION_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    event_type = (
        "manual_override"
        if rules_selected_torrent is not None and approved_torrent.id != rules_selected_torrent.id
        else "rule_accept"
    )
    payload = {
        "logged_at": datetime.now(UTC).isoformat(),
        "event_type": event_type,
        "request": {
            "id": request.id,
            "title": request.title,
            "media_type": request.media_type.value,
            "tmdb_id": request.tmdb_id,
            "tvdb_id": request.tvdb_id,
            "year": request.year,
        }
        if request is not None
        else None,
        "approved_torrent": _build_torrent_payload(approved_torrent),
        "rules_selected_torrent": _build_torrent_payload(rules_selected_torrent),
    }
    with STAGING_DECISION_LOG_PATH.open("a", encoding="utf-8") as file_handle:
        file_handle.write(json.dumps(payload, sort_keys=True))
        file_handle.write("\n")


def log_replacement_decision(
    *,
    request: Request | None,
    new_torrent: StagedTorrent,
    replaced_torrent: StagedTorrent,
    reason: str | None = None,
) -> None:
    """Append a replacement decision when an approved torrent is replaced."""
    STAGING_DECISION_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "logged_at": datetime.now(UTC).isoformat(),
        "event_type": "replacement",
        "request": {
            "id": request.id,
            "title": request.title,
            "media_type": request.media_type.value,
            "tmdb_id": request.tmdb_id,
            "tvdb_id": request.tvdb_id,
            "year": request.year,
        }
        if request is not None
        else None,
        "new_torrent": _build_torrent_payload(new_torrent),
        "replaced_torrent": _build_torrent_payload(replaced_torrent),
        "reason": reason,
    }
    with STAGING_DECISION_LOG_PATH.open("a", encoding="utf-8") as file_handle:
        file_handle.write(json.dumps(payload, sort_keys=True))
        file_handle.write("\n")
