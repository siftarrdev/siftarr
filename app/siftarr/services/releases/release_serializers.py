"""Release payload serialization, sorting, and finalization for dashboard responses."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, cast

from app.siftarr.models.request import MediaType
from app.siftarr.services.decisions.rule_engine import ReleaseEvaluation
from app.siftarr.services.integrations.prowlarr_service import ProwlarrRelease
from app.siftarr.services.releases.release_parser import (
    ParsedReleaseCoverage,
    cached_parse_release_coverage,
    is_exact_single_episode_release,
    is_multi_episode_release,
    parse_stored_release_coverage,
)
from app.siftarr.services.utils.type_utils import (
    coerce_int_list,
    normalize_float,
    normalize_int,
    normalize_optional_text,
)

SerializedObject = Mapping[str, object]


def format_release_size(size_bytes: int) -> str:
    """Format bytes as a compact human-readable size."""
    if size_bytes <= 0:
        return "Unknown"
    gib = size_bytes / 1024 / 1024 / 1024
    return f"{gib:.2f} GB"


def release_failed_size_limit(release: dict[str, object]) -> bool:
    """Return True when the release failed a size-limit rule."""
    rejection_reason = release.get("rejection_reason")
    return isinstance(rejection_reason, str) and rejection_reason.startswith("Size ")


def _match_effect(match: object) -> str | None:
    effect = getattr(match, "effect", None)
    if isinstance(effect, str):
        return effect
    if isinstance(match, Mapping):
        match_mapping = cast(Mapping[object, object], match)
        value = match_mapping.get("effect")
        return value if isinstance(value, str) else None
    return None


def _match_rule_type(match: object) -> str | None:
    rule_type = getattr(match, "rule_type", None)
    if isinstance(rule_type, str):
        return rule_type
    if isinstance(match, Mapping):
        match_mapping = cast(Mapping[object, object], match)
        value = match_mapping.get("rule_type")
        return value if isinstance(value, str) else None
    return None


def _match_matched(match: object) -> bool | None:
    value = getattr(match, "matched", None)
    if isinstance(match, Mapping):
        match_mapping = cast(Mapping[object, object], match)
        value = match_mapping.get("matched")
    return value if isinstance(value, bool) else None


def _derive_size_passed_from_matches(matches: object) -> bool | None:
    if not isinstance(matches, list):
        return None
    saw_size_rule = False
    for match in matches:
        if _match_rule_type(match) != "size_limit" and _match_effect(match) != "size_limit":
            continue
        saw_size_rule = True
        if _match_matched(match) is False:
            return False
    return True if saw_size_rule else None


def apply_release_size_per_season_metadata(
    release: dict[str, object],
) -> dict[str, object]:
    """Attach derived per-season size metadata when season coverage is known."""
    size_bytes = normalize_int(release.get("size_bytes"))
    covered_seasons = coerce_int_list(release.get("covered_seasons"))
    known_total_seasons = normalize_int(release.get("known_total_seasons"))
    covered_season_count = normalize_int(release.get("covered_season_count"))
    size_limit_passed = _derive_size_passed_from_matches(release.get("matches"))
    if size_limit_passed is None:
        legacy_size_passed = release.get("size_passed")
        size_limit_passed = legacy_size_passed if isinstance(legacy_size_passed, bool) else None

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
    release["size_per_season"] = format_release_size(size_per_season_bytes)
    release["size_per_season_bytes"] = size_per_season_bytes
    release["size_per_season_passed"] = size_limit_passed
    return release


def _derive_size_passed(evaluation: ReleaseEvaluation | Any) -> bool | None:
    """Derive size_passed from evaluation data alone.

    Returns False if the rejection reason starts with "Size ", True if the
    evaluation passed and there is no size rejection, or None if the evaluation
    did not pass and no size-limit information is available.
    """
    match_size_passed = _derive_size_passed_from_matches(getattr(evaluation, "matches", None))
    if match_size_passed is not None:
        return match_size_passed
    rejection_reason = getattr(evaluation, "rejection_reason", None)
    if isinstance(rejection_reason, str) and rejection_reason.startswith("Size "):
        return False
    return None


def serialize_rule_match(match: object) -> dict[str, object]:
    return {
        "rule_name": getattr(match, "rule_name", ""),
        "matched": getattr(match, "matched", False),
        "score_delta": getattr(match, "score_delta", 0),
        "rule_type": getattr(match, "rule_type", None),
        "effect": getattr(match, "effect", None),
    }


def compact_rule_evidence(evaluation: ReleaseEvaluation | Any) -> dict[str, object]:
    """Return durable, compact rule evidence for storage/history."""
    return {
        "passed": bool(getattr(evaluation, "passed", False)),
        "score": normalize_int(getattr(evaluation, "total_score", 0)),
        "rejection_reason": normalize_optional_text(getattr(evaluation, "rejection_reason", None)),
        "size_passed": _derive_size_passed(evaluation),
        "matches": [serialize_rule_match(match) for match in getattr(evaluation, "matches", [])],
    }


def compact_candidate_snapshot(
    evaluation: ReleaseEvaluation | Any,
    *,
    stored_release_id: int | None = None,
) -> dict[str, object]:
    """Serialize a retention-safe candidate snapshot without raw download URLs."""
    release = evaluation.release
    coverage = cached_parse_release_coverage(release.title)
    return {
        "title": release.title,
        "size_bytes": release.size,
        "size": format_release_size(release.size),
        "seeders": release.seeders,
        "leechers": release.leechers,
        "indexer": release.indexer,
        "resolution": release.resolution,
        "codec": release.codec,
        "release_group": release.release_group,
        "uploaded_by": release.uploaded_by,
        "info_hash": release.info_hash,
        "score": normalize_int(getattr(evaluation, "total_score", 0)),
        "passed": bool(getattr(evaluation, "passed", False)),
        "status": "passed" if getattr(evaluation, "passed", False) else "rejected",
        "rejection_reason": normalize_optional_text(getattr(evaluation, "rejection_reason", None)),
        "stored_release_id": stored_release_id,
        "rule_evidence": compact_rule_evidence(evaluation),
        "parse_metadata": {
            "season_number": coverage.season_number,
            "episode_number": coverage.episode_number,
            "season_numbers": list(coverage.season_numbers),
            "is_complete_series": coverage.is_complete_series,
        },
    }


def serialize_evaluated_release(
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
        "size": format_release_size(release.size),
        "seeders": release.seeders,
        "leechers": release.leechers,
        "indexer": release.indexer,
        "resolution": release.resolution,
        "codec": release.codec,
        "release_group": release.release_group,
        "uploaded_by": release.uploaded_by,
        "info_hash": release.info_hash,
        "score": evaluation.total_score,
        "passed": evaluation.passed,
        "status": status,
        "status_label": "Passed" if evaluation.passed else "Rejected",
        "rejection_reason": normalize_optional_text(getattr(evaluation, "rejection_reason", None)),
        "download_url": release.download_url,
        "magnet_url": release.magnet_url,
        "publish_date": release.publish_date.isoformat() if release.publish_date else None,
        "stored_release_id": None,
        "size_passed": _derive_size_passed(evaluation),
        "matches": [serialize_rule_match(match) for match in getattr(evaluation, "matches", [])],
        "rule_evidence": compact_rule_evidence(evaluation),
        "files": getattr(release, "files", None),
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

    return apply_release_size_per_season_metadata(payload)


def serialize_stored_evaluated_release(
    release: Any,
    evaluation: ReleaseEvaluation | Any,
    *,
    media_type: MediaType,
) -> dict[str, object]:
    """Serialize a persisted release plus extra dashboard metadata."""
    coverage = None
    if media_type == MediaType.TV:
        coverage = parse_stored_release_coverage(
            release.season_coverage,
            release.season_number,
            release.episode_number,
        )

    payload = serialize_evaluated_release(release, evaluation, coverage=coverage)
    payload.update(
        {
            "score": release.score,
            "passed": release.passed_rules,
            "rejection_reason": getattr(evaluation, "rejection_reason", None),
            "season_number": release.season_number,
            "episode_number": release.episode_number,
            "matches": release.rule_evidence.get("matches", [])
            if getattr(release, "rule_evidence", None)
            else [serialize_rule_match(match) for match in getattr(evaluation, "matches", [])],
            "rule_evidence": getattr(release, "rule_evidence", None)
            or compact_rule_evidence(evaluation),
            "target_scope": serialize_target_scope(
                media_type=media_type,
                title=release.title,
                season_number=release.season_number,
                episode_number=release.episode_number,
                season_coverage=release.season_coverage,
            ),
        }
    )
    return payload


def serialize_target_scope(
    *,
    media_type: MediaType,
    title: str,
    season_number: int | None = None,
    episode_number: int | None = None,
    season_coverage: str | None = None,
) -> dict[str, object]:
    """Serialize lightweight targeting metadata for releases and staged torrents."""
    if media_type != MediaType.TV:
        return {"type": "request"}

    coverage = (
        cached_parse_release_coverage(title)
        if season_coverage is None
        else parse_stored_release_coverage(season_coverage, season_number, episode_number)
    )
    scoped_season_number = coverage.season_number
    scoped_episode_number = coverage.episode_number

    if (
        scoped_season_number is not None
        and scoped_episode_number is not None
        and is_exact_single_episode_release(title, scoped_season_number, scoped_episode_number)
    ):
        return {
            "type": "single_episode",
            "season_number": scoped_season_number,
            "episode_number": scoped_episode_number,
        }

    if scoped_season_number is not None and scoped_episode_number is not None:
        if is_multi_episode_release(title):
            return {
                "type": "multi_episode_pack",
                "season_number": scoped_season_number,
                "first_episode_number": scoped_episode_number,
            }
        return {"type": "unknown"}

    if coverage.is_complete_series:
        return {"type": "complete_series"}

    covered_seasons = list(coverage.season_numbers)
    if len(covered_seasons) == 1:
        return {"type": "season_pack", "season_numbers": covered_seasons}
    if len(covered_seasons) > 1:
        return {"type": "multi_season_pack", "season_numbers": covered_seasons}

    return {"type": "unknown"}


def scope_to_episode_set(
    scope: object,
    known_season_numbers: list[int] | None = None,
) -> set[tuple[int, int | None]]:
    """Convert a target_scope dict to a set of (season_number, episode_number) tuples.

    ``single_episode`` scopes return the exact episode pair.
    ``season_pack``/``multi_season_pack`` scopes return (s, None) for each covered season
    (episode granularity is unknown for packs).
    ``complete_series`` scopes use ``known_season_numbers`` if provided, otherwise empty.
    ``unknown`` and non-Mapping inputs return an empty set.
    """
    if not isinstance(scope, dict):
        return set()
    scope_dict: dict[str, object] = cast(dict[str, object], scope)
    scope_type = scope_dict.get("type")
    if scope_type == "single_episode":
        sn = scope_dict.get("season_number")
        en = scope_dict.get("episode_number")
        if isinstance(sn, int) and isinstance(en, int):
            return {(sn, en)}
        return set()
    if scope_type in {"season_pack", "multi_season_pack"}:
        season_numbers = scope_dict.get("season_numbers")
        if isinstance(season_numbers, list):
            return {(s, None) for s in season_numbers if isinstance(s, int)}
        return set()
    if scope_type == "complete_series":
        seasons = known_season_numbers or []
        return {(s, None) for s in seasons if isinstance(s, int)}
    return set()


def _episode_sets_overlap(
    left_set: set[tuple[int, int | None]], right_set: set[tuple[int, int | None]]
) -> bool:
    """Check if two episode sets overlap, treating ``None`` as a wildcard.

    ``(s, None)`` represents coverage of an entire season ``s``, so it overlaps
    with any ``(s, e)`` for that season.
    """
    for ls, le in left_set:
        for rs, re in right_set:
            if ls == rs and (le is None or re is None or le == re):
                return True
    return False


def tv_target_scopes_overlap(
    left_scope: SerializedObject | None,
    right_scope: SerializedObject | None,
    known_season_numbers: list[int] | None = None,
) -> bool:
    """Return True when a candidate TV scope should replace an active scope.

    Both scopes are converted to episode sets via ``scope_to_episode_set()``
    and checked for overlap via ``_episode_sets_overlap()``.  If either set is
    empty (e.g. unknown scope or missing data) the function returns True
    conservatively.
    """
    left_set = scope_to_episode_set(left_scope, known_season_numbers)
    right_set = scope_to_episode_set(right_scope, known_season_numbers)
    if not left_set or not right_set:
        return True  # Conservative: assume overlap when scope is unclear
    return _episode_sets_overlap(left_set, right_set)


def _scope_seasons(scope: SerializedObject) -> set[int]:
    """Extract season numbers from a scope via ``scope_to_episode_set``."""
    return {s for (s, e) in scope_to_episode_set(scope)}


def _load_staged_release_identity(staged_torrent: Any) -> dict[str, object]:
    json_path = getattr(staged_torrent, "json_path", None)
    if not json_path:
        return {}
    try:
        with open(json_path) as f:
            metadata = json.load(f)
    except OSError, json.JSONDecodeError, TypeError:
        return {}
    release = metadata.get("release")
    return release if isinstance(release, dict) else {}


def serialize_active_staged_torrent(
    staged_torrent: Any,
    *,
    media_type: MediaType,
) -> dict[str, object]:
    """Serialize staged-torrent metadata for dashboard selection state."""
    coverage = cached_parse_release_coverage(staged_torrent.title)
    release_identity = _load_staged_release_identity(staged_torrent)
    payload = {
        "id": staged_torrent.id,
        "title": staged_torrent.title,
        "status": staged_torrent.status,
        "selection_source": staged_torrent.selection_source,
        "target_scope": serialize_target_scope(
            media_type=media_type,
            title=staged_torrent.title,
            season_number=coverage.season_number,
            episode_number=coverage.episode_number,
        ),
    }
    info_hash = release_identity.get("info_hash")
    model_magnet = getattr(staged_torrent, "magnet_url", None)
    magnet_url = (
        model_magnet if isinstance(model_magnet, str) else release_identity.get("magnet_url")
    )
    if isinstance(info_hash, str) and info_hash:
        payload["info_hash"] = info_hash
    if isinstance(magnet_url, str) and magnet_url:
        payload["magnet_url"] = magnet_url
    return payload


def release_matches_active_stage(
    release: SerializedObject,
    active_stage: SerializedObject,
    *,
    media_type: MediaType,
) -> bool:
    """Return True when a serialized release matches an active staged torrent."""
    if media_type != MediaType.TV:
        return release.get("title") == active_stage.get("title")

    release_scope = _as_serialized_object(release.get("target_scope"))
    active_scope = _as_serialized_object(active_stage.get("target_scope"))
    scopes_match = release_scope == active_scope

    release_info_hash = release.get("info_hash")
    active_info_hash = active_stage.get("info_hash")
    if release_info_hash and active_info_hash:
        return release_info_hash == active_info_hash and scopes_match

    release_magnet = release.get("magnet_url")
    active_magnet = active_stage.get("magnet_url")
    if release_magnet and active_magnet:
        return release_magnet == active_magnet and scopes_match

    if (
        release_scope is not None
        and active_scope is not None
        and release_scope.get("type") == active_scope.get("type") == "single_episode"
    ):
        scope_matches = release_scope.get("season_number") == active_scope.get(
            "season_number"
        ) and release_scope.get("episode_number") == active_scope.get("episode_number")
        return scope_matches and release.get("title") == active_stage.get("title")

    return scopes_match and release.get("title") == active_stage.get("title")


def apply_active_selection_metadata(
    releases: list[dict[str, object]],
    active_staged_payloads: list[dict[str, object]],
    *,
    media_type: MediaType,
) -> list[dict[str, object]]:
    """Attach active-staged selection metadata to serialized releases."""
    for release in releases:
        matching_active_stage = next(
            (
                active_stage
                for active_stage in active_staged_payloads
                if release_matches_active_stage(release, active_stage, media_type=media_type)
            ),
            None,
        )
        release["is_active_selection"] = matching_active_stage is not None
        release["conflicts_active_selection"] = (
            any(
                tv_target_scopes_overlap(
                    _as_serialized_object(release.get("target_scope")),
                    _as_serialized_object(active_stage.get("target_scope")),
                )
                for active_stage in active_staged_payloads
            )
            if media_type == MediaType.TV
            else bool(active_staged_payloads)
        )
        release["active_selection_status"] = (
            matching_active_stage.get("status") if matching_active_stage else None
        )
        release["active_selection_source"] = (
            matching_active_stage.get("selection_source") if matching_active_stage else None
        )
        release["active_staged_torrent"] = matching_active_stage
    return releases


def _as_serialized_object(value: object) -> SerializedObject | None:
    """Return mapping values with object payloads for typed key access."""
    if not isinstance(value, Mapping):
        return None
    return cast(SerializedObject, value)


def dashboard_release_sort_key(release: dict[str, object]) -> tuple[float, float, int, float, str]:
    """Sort dashboard releases by score desc, size asc, then stable tie-breakers."""
    score = normalize_float(release.get("score"))
    size_bytes = release.get("_size_bytes")
    normalized_size = (
        float(size_bytes)
        if isinstance(size_bytes, int | float) and size_bytes >= 0
        else float("inf")
    )
    seeders = normalize_int(release.get("seeders"))
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


def season_pack_release_sort_key(
    release: dict[str, object],
) -> tuple[int, float, float, int, float, str]:
    """Sort season-pack releases with passing size limits first."""
    size_limit_priority = 1 if release_failed_size_limit(release) else 0
    return (size_limit_priority, *dashboard_release_sort_key(release))


def finalize_releases(
    releases: list[dict[str, object]],
    *,
    sort_key=None,
) -> list[dict[str, object]]:
    """Apply shared dashboard ordering and remove internal sort metadata.

    Defaults to dashboard_release_sort_key. Pass sort_key=season_pack_release_sort_key
    for season-pack ordering.
    """
    if sort_key is None:
        sort_key = dashboard_release_sort_key
    ordered = sorted(releases, key=sort_key)
    for release in ordered:
        release.pop("_size_bytes", None)
    return ordered
