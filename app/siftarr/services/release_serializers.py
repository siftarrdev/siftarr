"""Release payload serialization, sorting, and finalization for dashboard responses."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, cast

from app.siftarr.models.request import MediaType
from app.siftarr.services.prowlarr_service import ProwlarrRelease
from app.siftarr.services.release_parser import (
    ParsedReleaseCoverage,
    is_exact_single_episode_release,
    parse_release_coverage,
    parse_stored_release_coverage,
)
from app.siftarr.services.rule_engine import ReleaseEvaluation
from app.siftarr.services.type_utils import (
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


def apply_release_size_per_season_metadata(
    release: dict[str, object],
) -> dict[str, object]:
    """Attach derived per-season size metadata when season coverage is known."""
    size_bytes = normalize_int(release.get("size_bytes"))
    covered_seasons = coerce_int_list(release.get("covered_seasons"))
    known_total_seasons = normalize_int(release.get("known_total_seasons"))
    covered_season_count = normalize_int(release.get("covered_season_count"))
    size_limit_passed = release.get("passed")

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
    release["size_per_season_passed"] = (
        None if size_limit_passed is None else not release_failed_size_limit(release)
    )
    return release


def _derive_size_passed(evaluation: ReleaseEvaluation | Any) -> bool | None:
    """Derive size_passed from evaluation data alone.

    Returns False if the rejection reason starts with "Size ", True if the
    evaluation passed and there is no size rejection, or None if the evaluation
    did not pass and no size-limit information is available.
    """
    rejection_reason = getattr(evaluation, "rejection_reason", None)
    if isinstance(rejection_reason, str) and rejection_reason.startswith("Size "):
        return False
    return True if evaluation.passed else None


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
            "matches": [
                {
                    "rule_name": match.rule_name,
                    "matched": match.matched,
                    "score_delta": match.score_delta,
                }
                for match in getattr(evaluation, "matches", [])
            ],
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

    coverage = parse_stored_release_coverage(season_coverage, season_number, episode_number)
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

    return {"type": "broad"}


def serialize_active_staged_torrent(
    staged_torrent: Any,
    *,
    media_type: MediaType,
) -> dict[str, object]:
    """Serialize staged-torrent metadata for dashboard selection state."""
    coverage = parse_release_coverage(staged_torrent.title)
    return {
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
    if (
        release_scope is not None
        and active_scope is not None
        and release_scope.get("type") == active_scope.get("type") == "single_episode"
    ):
        return release_scope.get("season_number") == active_scope.get(
            "season_number"
        ) and release_scope.get("episode_number") == active_scope.get("episode_number")

    return release.get("title") == active_stage.get("title")


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
