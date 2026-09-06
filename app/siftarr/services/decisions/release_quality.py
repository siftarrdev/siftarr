"""Built-in, non-rejecting release quality signals."""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.siftarr.services.integrations.prowlarr_service import ProwlarrRelease
from app.siftarr.services.releases.release_parser import (
    cached_parse_release_coverage,
    is_exact_single_episode_release,
)

_ARCHIVE_RE = re.compile(r"(?:^|/)[^/]+\.(?:rar|r\d{2}|7z|zip)(?:$)", re.IGNORECASE)
_RAR_TITLE_RE = re.compile(r"(?<![A-Z0-9])RAR(?![A-Z0-9])", re.IGNORECASE)
_NORAR_RE = re.compile(r"(?<![A-Z0-9])NO[\s._-]*RAR(?![A-Z0-9])", re.IGNORECASE)
_COLLECTION_RE = re.compile(
    r"(?:^|[. _-])(?:collection|trilogy|quadrilogy|duology|anthology|complete)(?:$|[. _-])",
    re.IGNORECASE,
)
_PLAYABLE_EXTENSIONS = (".mkv", ".mp4", ".avi", ".m4v", ".ts", ".wmv", ".mov")


@dataclass(frozen=True, slots=True)
class ReleaseQualityPolicy:
    media_type: str
    file_count_bonus: int = 5
    high_file_count_penalty: int = 10
    archive_penalty: int = 100
    rar_title_penalty: int = 50
    low_seed_penalty: int = 40
    marginal_seed_penalty: int = 10
    episode_fallback_max_size_bytes: int = 3 * 1024**3


@dataclass(frozen=True, slots=True)
class QualitySignal:
    name: str
    score_delta: int


def _is_scoped_single_item(release: ProwlarrRelease, media_type: str) -> bool:
    coverage = cached_parse_release_coverage(release.title)
    if media_type.casefold() == "movie":
        return not (
            coverage.season_numbers
            or coverage.episode_number is not None
            or coverage.is_complete_series
            or _COLLECTION_RE.search(release.title)
        )
    if media_type.casefold() != "tv":
        return False
    return bool(
        coverage.season_number is not None
        and coverage.episode_number is not None
        and is_exact_single_episode_release(
            release.title, coverage.season_number, coverage.episode_number
        )
    )


def _has_main_payload_archive(paths: tuple[str, ...] | list[str]) -> bool:
    """Archives are suspicious only when no normal playable payload is present."""
    normalized = [path.replace("\\", "/") for path in paths]
    has_archive = any(_ARCHIVE_RE.search(path) for path in normalized)
    has_playable = any(
        path.casefold().endswith(_PLAYABLE_EXTENSIONS)
        and not any(
            re.search(r"(?<![a-z0-9])sample(?![a-z0-9])", component, re.IGNORECASE)
            for component in path.split("/")
        )
        for path in normalized
    )
    return has_archive and not has_playable


def quality_signals(release: ProwlarrRelease, policy: ReleaseQualityPolicy) -> list[QualitySignal]:
    """Return bounded score-only signals. None of these signals rejects a release."""
    signals: list[QualitySignal] = []
    paths = release.file_paths or ()
    archive_payload = _has_main_payload_archive(paths)
    norar = bool(_NORAR_RE.search(release.title))

    if archive_payload:
        signals.append(QualitySignal("Archive main payload", -abs(policy.archive_penalty)))
    elif not norar and _RAR_TITLE_RE.search(release.title):
        signals.append(QualitySignal("RAR title evidence", -abs(policy.rar_title_penalty)))

    if _is_scoped_single_item(release, policy.media_type) and not archive_payload:
        count = release.files
        if isinstance(count, int) and not isinstance(count, bool) and count > 0:
            if count <= 5:
                signals.append(QualitySignal("Compact file list", abs(policy.file_count_bonus)))
            elif count > 10 and not norar:
                complete_paths = bool(paths) and len(paths) == count
                known_playable_nonarchive = complete_paths and any(
                    path.casefold().endswith(_PLAYABLE_EXTENSIONS)
                    and not re.search(r"(?<![a-z0-9])sample(?![a-z0-9])", path, re.IGNORECASE)
                    for path in paths
                )
                if not known_playable_nonarchive:
                    signals.append(
                        QualitySignal("High file count", -abs(policy.high_file_count_penalty))
                    )

    if release.seeders in (1, 2):
        signals.append(QualitySignal("Low seed availability", -abs(policy.low_seed_penalty)))
    elif release.seeders in (3, 4):
        signals.append(
            QualitySignal("Marginal seed availability", -abs(policy.marginal_seed_penalty))
        )
    return signals
