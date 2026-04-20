"""Release payload serialization, sorting, and finalization for dashboard responses."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from app.siftarr.services.prowlarr_service import ProwlarrRelease
from app.siftarr.services.release_parser import ParsedReleaseCoverage
from app.siftarr.services.rule_engine import ReleaseEvaluation

if TYPE_CHECKING:
    from app.siftarr.services.rule_engine import RuleEngine
from app.siftarr.services.type_utils import (
    coerce_int_list,
    normalize_float,
    normalize_int,
    normalize_optional_text,
)


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
    rule_engine: RuleEngine | None = None,
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
    if rule_engine is not None:
        release["size_per_season_passed"] = rule_engine.evaluate_per_season_size(
            size_per_season_bytes
        )
    else:
        release["size_per_season_passed"] = (
            None if size_limit_passed is None else not release_failed_size_limit(release)
        )
    return release


def _derive_size_passed(evaluation: ReleaseEvaluation | Any) -> bool | None:
    """Derive size_passed from evaluation data alone.

    Returns False if the rejection reason starts with "Size ", True if there are
    size-limit matches that passed, or None if no size-limit information is available.
    """
    rejection_reason = getattr(evaluation, "rejection_reason", None)
    if isinstance(rejection_reason, str) and rejection_reason.startswith("Size "):
        return False
    # Size-limit rules don't produce entries in evaluation.matches (they set
    # rejection_reason directly).  If the release didn't fail a size check we
    # can't tell from matches alone whether any size rule was even evaluated, so
    # return None to signal "unknown / no size annotation".
    return None


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
