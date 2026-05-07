"""Canonical derivation functions for TV episode/season/request statuses.

Episode status is the sole ground truth for TV content availability.
Season and request statuses are **derived upward** from episodes —
they are cached mirrors written only by :func:`recompute_tv_statuses`.

Pure functions in this module have no database access.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import TYPE_CHECKING

from app.siftarr.models.request import Request, RequestStatus

if TYPE_CHECKING:
    from app.siftarr.models.episode import Episode


def derive_episode_status(*, is_on_plex: bool, air_date: date | None) -> RequestStatus:
    """Derive an episode status from Plex availability and air date."""
    if is_on_plex:
        return RequestStatus.COMPLETED
    if isinstance(air_date, date) and air_date > datetime.now(UTC).date():
        return RequestStatus.UNRELEASED
    return RequestStatus.PENDING


def episodes_are_unreleased(episodes: list[Episode]) -> bool:
    """Return whether any episode has a future air date.

    Silently skips episodes where ``air_date`` is not a ``date`` instance
    (e.g. MagicMock in tests).
    """
    today = datetime.now(UTC).date()
    for ep in episodes:
        air_date = ep.air_date
        if isinstance(air_date, date) and air_date > today:
            return True
    return False


def derive_season_status(episodes: list[Episode]) -> RequestStatus:
    """Derive season status from episode statuses (highest-precedence-first).

    Precedence (highest → lowest):
      SEARCHING > DOWNLOADING > STAGED > COMPLETED > FAILED > DENIED > UNRELEASED > PENDING
    """
    if not episodes:
        return RequestStatus.PENDING

    statuses = {ep.status for ep in episodes}

    for candidate in (
        RequestStatus.SEARCHING,
        RequestStatus.DOWNLOADING,
        RequestStatus.STAGED,
    ):
        if candidate in statuses:
            return candidate

    # ── Terminal state detection ─────────────────────────────────
    if statuses == {RequestStatus.COMPLETED}:
        return RequestStatus.COMPLETED

    # Mixed completed with anything non-terminal → PENDING
    if RequestStatus.COMPLETED in statuses:
        non_terminal = statuses - {RequestStatus.COMPLETED, RequestStatus.FAILED, RequestStatus.DENIED}
        if non_terminal:
            return RequestStatus.PENDING

    # Any PENDING episode → treat as in-progress
    if RequestStatus.PENDING in statuses:
        return RequestStatus.PENDING

    if RequestStatus.FAILED in statuses:
        return RequestStatus.FAILED
    if RequestStatus.DENIED in statuses:
        return RequestStatus.DENIED

    # Only unreleased episodes remain
    if episodes_are_unreleased(episodes):
        return RequestStatus.UNRELEASED

    return RequestStatus.PENDING


def derive_request_status_from_episodes(episodes: list[Episode]) -> RequestStatus:
    """Derive aggregate TV request status from episode statuses."""
    return derive_season_status(episodes)


def recompute_tv_request_status(request: Request) -> RequestStatus:
    """Recompute ``Request.status`` for a TV show from its episode tree.

    Loads all episodes (via ``request.seasons[*].episodes``) and derives
    the aggregate status.  Call this after any episode mutation so the
    request-level status stays in sync.
    """
    all_episodes: list[Episode] = []
    for season in request.seasons:
        all_episodes.extend(season.episodes)
    return derive_request_status_from_episodes(all_episodes)


def recompute_tv_season_statuses(request: Request) -> dict[int, RequestStatus]:
    """Recompute all ``Season.status`` values from their episodes.

    Returns a ``{season_number: status}`` map so callers can decide
    whether to persist or just inspect.
    """
    result: dict[int, RequestStatus] = {}
    for season in request.seasons:
        result[season.season_number] = derive_season_status(list(season.episodes))
    return result


def derive_tv_display_label(episodes: list[Episode]) -> str:
    """Return a short human-readable label for dashboard cards.

    Examples: ``"completed"``, ``"partial"``, ``"downloading"``, ``"pending"``.
    """
    if not episodes:
        return "pending"

    total = len(episodes)
    completed = sum(1 for ep in episodes if ep.status == RequestStatus.COMPLETED)
    downloading = sum(1 for ep in episodes if ep.status == RequestStatus.DOWNLOADING)
    staged = sum(1 for ep in episodes if ep.status == RequestStatus.STAGED)
    pending = sum(1 for ep in episodes if ep.status == RequestStatus.PENDING)

    if completed == total:
        return "completed"
    if downloading:
        return "downloading"
    if staged:
        return "staged"
    if pending == total:
        return "pending"
    if completed > 0:
        return "partial"

    return "pending"
