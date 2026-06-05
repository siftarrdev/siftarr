"""Canonical JSONL staging decision log service."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from app.siftarr.models.request import MediaType, Request
from app.siftarr.models.staged_torrent import StagedTorrent
from app.siftarr.services.decisions.rule_engine import ReleaseEvaluation
from app.siftarr.services.releases.release_serializers import (
    serialize_evaluated_release,
    serialize_target_scope,
)

STAGING_DECISION_LOG_PATH = Path("/data/staging/decision-log.jsonl")
SCHEMA_VERSION = 1
RETENTION_DAYS = 120


def retention_cutoff(now: datetime | None = None) -> datetime:
    base = now or datetime.now(UTC)
    if base.tzinfo is None:
        base = base.replace(tzinfo=UTC)
    return base.astimezone(UTC) - timedelta(days=RETENTION_DAYS)


def _parse_dt(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _is_retained(entry: Mapping[str, Any], cutoff: datetime | None = None) -> bool:
    logged_at = _parse_dt(entry.get("logged_at"))
    return logged_at is None or logged_at >= (cutoff or retention_cutoff())


def _request_payload(request: Request | None) -> dict[str, Any] | None:
    if request is None:
        return None
    media_type = request.media_type.value if hasattr(request.media_type, "value") else request.media_type
    return {
        "id": request.id,
        "title": request.title,
        "media_type": media_type,
        "tmdb_id": request.tmdb_id,
        "tvdb_id": request.tvdb_id,
        "year": request.year,
    }


def staged_torrent_payload(torrent: StagedTorrent | None) -> dict[str, Any] | None:
    if torrent is None:
        return None
    return {
        "id": torrent.id,
        "title": torrent.title,
        "score": torrent.score,
        "size": torrent.size,
        "size_bytes": torrent.size,
        "indexer": torrent.indexer,
        "status": torrent.status,
        "selection_source": torrent.selection_source,
        "magnet_url": getattr(torrent, "magnet_url", None),
        "category": "selected",
    }


def _candidate_from_mapping(value: object, *, category: str | None = None) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    payload = dict(value)
    if "size" in payload and "size_bytes" not in payload and isinstance(payload["size"], int):
        payload["size_bytes"] = payload["size"]
    if category is not None:
        payload["category"] = category
    return payload


def _candidate_from_evaluation(
    evaluation: ReleaseEvaluation,
    *,
    category: str,
    media_type: MediaType,
) -> dict[str, Any]:
    payload = serialize_evaluated_release(evaluation.release, evaluation)
    payload["category"] = category
    payload["outcome"] = "passed" if evaluation.passed else "rejected"
    payload["score_deltas"] = [
        {"rule_name": m.get("rule_name"), "delta": m.get("score_delta")}
        for m in payload.get("matches", [])
        if isinstance(m, Mapping) and m.get("score_delta")
    ]
    payload["rule_matches"] = payload.get("matches", [])
    payload["size_checks"] = {"passed": payload.get("size_passed")}
    payload["quality_parse"] = {
        "resolution": payload.get("resolution"),
        "codec": payload.get("codec"),
        "release_group": payload.get("release_group"),
    }
    payload["target_scope"] = serialize_target_scope(media_type=media_type, title=evaluation.release.title)
    return payload


def build_decision_entry(
    *,
    event_type: str,
    outcome: str,
    request: Request | None,
    selection: Mapping[str, Any] | None = None,
    selected_release: Mapping[str, Any] | None = None,
    rules_selected_release: Mapping[str, Any] | None = None,
    top_candidates: Iterable[Mapping[str, Any]] | None = None,
    all_candidates: Iterable[Mapping[str, Any]] | None = None,
    failures: Iterable[Mapping[str, Any]] | None = None,
    counts: Mapping[str, Any] | None = None,
    indexer_stats: Mapping[str, Any] | None = None,
    search_context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "logged_at": datetime.now(UTC).isoformat(),
        "event_type": event_type,
        "outcome": outcome,
        "request": _request_payload(request),
        "selection": dict(selection or {}),
        "selected_release": dict(selected_release) if selected_release else None,
        "rules_selected_release": dict(rules_selected_release) if rules_selected_release else None,
        "top_candidates": [dict(c) for c in (top_candidates or [])],
        "all_candidates": [dict(c) for c in (all_candidates or [])],
        "failures": [dict(f) for f in (failures or [])],
        "counts": dict(counts or {}),
        "indexer_stats": dict(indexer_stats or {}),
        "search_context": dict(search_context or {}),
    }


def append_entry(entry: Mapping[str, Any], path: Path | None = None) -> None:
    log_path = path or STAGING_DECISION_LOG_PATH
    normalized = normalize_entry(entry)
    if not _is_retained(normalized):
        return
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as file_handle:
            file_handle.write(json.dumps(normalized, sort_keys=True, default=str))
            file_handle.write("\n")
    except OSError:
        return


def log_staging_decision(
    *, request: Request | None, approved_torrent: StagedTorrent, rules_selected_torrent: StagedTorrent | None
) -> None:
    approved = staged_torrent_payload(approved_torrent)
    rules = staged_torrent_payload(rules_selected_torrent)
    event_type = (
        "manual_override"
        if rules_selected_torrent is not None and approved_torrent.id != rules_selected_torrent.id
        else "rule_accept"
    )
    append_entry(
        build_decision_entry(
            event_type=event_type,
            outcome="approved",
            request=request,
            selection={"source": approved_torrent.selection_source, "selection_source": approved_torrent.selection_source},
            selected_release=approved,
            rules_selected_release=rules,
            top_candidates=[c for c in (approved, rules) if c],
        )
    )


def log_replacement_decision(
    *, request: Request | None, new_torrent: StagedTorrent, replaced_torrent: StagedTorrent, reason: str | None = None
) -> None:
    new_payload = staged_torrent_payload(new_torrent)
    replaced = staged_torrent_payload(replaced_torrent)
    if replaced:
        replaced["category"] = "replaced"
    append_entry(
        build_decision_entry(
            event_type="replacement",
            outcome="replaced",
            request=request,
            selection={"source": new_torrent.selection_source, "selection_source": new_torrent.selection_source, "reason": reason},
            selected_release=new_payload,
            top_candidates=[c for c in (new_payload, replaced) if c],
            failures=[{"reason": reason, "replaced_release": replaced}] if reason else [],
        )
    )


def log_evaluations(
    *,
    request: Request,
    event_type: str,
    outcome: str,
    evaluations: list[ReleaseEvaluation],
    selected: list[ReleaseEvaluation] | None = None,
    failures: list[Mapping[str, Any]] | None = None,
    counts: Mapping[str, Any] | None = None,
    indexer_stats: Mapping[str, Any] | None = None,
    search_context: Mapping[str, Any] | None = None,
) -> None:
    media_type = request.media_type if isinstance(request.media_type, MediaType) else MediaType(request.media_type)
    selected_set = {id(e) for e in (selected or [])}
    candidates = [
        _candidate_from_evaluation(e, category="selected" if id(e) in selected_set else ("top" if e.passed else "rejected"), media_type=media_type)
        for e in evaluations
    ]
    top = sorted(candidates, key=lambda c: float(c.get("score") or 0), reverse=True)[:25]
    selected_payloads = [
        _candidate_from_evaluation(e, category="selected", media_type=media_type) for e in (selected or [])
    ]
    append_entry(
        build_decision_entry(
            event_type=event_type,
            outcome=outcome,
            request=request,
            selection={"source": "rule", "selection_source": "rule"} if selected_payloads else {"source": "none"},
            selected_release=selected_payloads[0] if len(selected_payloads) == 1 else None,
            rules_selected_release=selected_payloads[0] if selected_payloads else None,
            top_candidates=top,
            all_candidates=candidates,
            failures=failures,
            counts=counts,
            indexer_stats=indexer_stats,
            search_context=search_context,
        )
        | ({"selected_releases": selected_payloads} if len(selected_payloads) > 1 else {})
    )


def _normalize_legacy(entry: Mapping[str, Any]) -> dict[str, Any]:
    approved = _candidate_from_mapping(entry.get("approved_torrent"), category="selected")
    rules = _candidate_from_mapping(entry.get("rules_selected_torrent"), category="top")
    new_torrent = _candidate_from_mapping(entry.get("new_torrent"), category="selected")
    replaced = _candidate_from_mapping(entry.get("replaced_torrent"), category="replaced")
    selected = approved or new_torrent or _candidate_from_mapping(entry.get("selected_release"), category="selected")
    event_type = str(entry.get("event_type") or "decision")
    return {
        "schema_version": SCHEMA_VERSION,
        "logged_at": entry.get("logged_at"),
        "event_type": event_type,
        "outcome": "replaced" if event_type == "replacement" else "approved",
        "request": entry.get("request"),
        "selection": {
            "source": (selected or {}).get("selection_source"),
            "selection_source": (selected or {}).get("selection_source"),
            "reason": entry.get("reason"),
        },
        "selected_release": selected,
        "rules_selected_release": rules,
        "top_candidates": [c for c in (selected, rules, replaced) if c],
        "all_candidates": [c for c in (selected, rules, replaced) if c],
        "failures": [{"reason": entry.get("reason"), "replaced_release": replaced}] if replaced else [],
        "counts": {},
        "indexer_stats": {},
        "search_context": {},
    }


def normalize_entry(entry: Mapping[str, Any]) -> dict[str, Any]:
    if entry.get("schema_version") == SCHEMA_VERSION:
        normalized = dict(entry)
    else:
        normalized = _normalize_legacy(entry)
    for key, default in {
        "selection": {}, "top_candidates": [], "all_candidates": [], "failures": [],
        "counts": {}, "indexer_stats": {}, "search_context": {},
    }.items():
        normalized.setdefault(key, default)
    return normalized


def read_entries(path: Path | None = None, *, include_expired: bool = False) -> list[dict[str, Any]]:
    log_path = path or STAGING_DECISION_LOG_PATH
    if not log_path.exists():
        return []
    entries: list[dict[str, Any]] = []
    cutoff = retention_cutoff()
    with log_path.open(encoding="utf-8") as file_handle:
        for line in file_handle:
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(raw, Mapping):
                continue
            normalized = normalize_entry(raw)
            if include_expired or _is_retained(normalized, cutoff):
                entries.append(normalized)
    return entries


def entry_sort_key(entry: Mapping[str, Any]) -> tuple[int, float]:
    parsed = _parse_dt(entry.get("logged_at"))
    return (1, parsed.timestamp()) if parsed else (0, 0.0)
