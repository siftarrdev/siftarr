"""Canonical derivation functions for TV episode/season/request statuses.

Episode status is the sole ground truth for TV content availability.
Season and request statuses are **derived upward** from episodes —
they are cached mirrors written only by :func:`recompute_tv_statuses`.

Pure functions in this module have no database access.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING

from app.siftarr.models.request import ACTIVE_STAGING_WORKFLOW_STATUSES, Request, RequestStatus

if TYPE_CHECKING:
    from app.siftarr.models.episode import Episode


def derive_episode_status(*, is_on_plex: bool, air_date: date | None) -> RequestStatus:
    """Derive an episode status from Plex availability and air date."""
    if is_on_plex:
        return RequestStatus.COMPLETED
    if isinstance(air_date, date) and air_date > datetime.now(UTC).date():
        return RequestStatus.UNRELEASED
    return RequestStatus.PENDING


def unaired_undated_episodes(
    numbered_episodes: Iterable[tuple[int, Episode]], *, today: date | None = None
) -> list[Episode]:
    """Return undated episodes that no later episode has aired after.

    TMDB lists announced episodes as placeholders with no air date. An undated
    episode counts as aired only when a later episode (higher number in the same
    season, or any episode in a later season) has a past air date or is on Plex.
    """
    today = today or datetime.now(UTC).date()
    numbered = list(numbered_episodes)
    latest_aired = max(
        (
            (season_number, ep.episode_number)
            for season_number, ep in numbered
            if (isinstance(ep.air_date, date) and ep.air_date <= today)
            or ep.status == RequestStatus.COMPLETED
        ),
        default=None,
    )
    return [
        ep
        for season_number, ep in numbered
        if not isinstance(ep.air_date, date)
        and ep.status != RequestStatus.COMPLETED
        and (latest_aired is None or (season_number, ep.episode_number) > latest_aired)
    ]


def episodes_are_unreleased(episodes: Sequence[Episode]) -> bool:
    """Return whether any episode has a future air date."""
    today = datetime.now(UTC).date()
    for ep in episodes:
        air_date = ep.air_date
        if air_date is not None and air_date > today:
            return True
    return False


def derive_season_status(episodes: Sequence[Episode]) -> RequestStatus:
    """Derive season status from episode statuses (highest-precedence-first).

    Precedence (highest → lowest):
      SEARCHING > DOWNLOADING > STAGED > COMPLETED > FAILED > DENIED > UNRELEASED > PENDING
    """
    if not episodes:
        return RequestStatus.PENDING

    statuses = {ep.status for ep in episodes}

    # Episode aggregation has display/workflow precedence, not a generic lifecycle
    # grouping: SEARCHING must outrank active staging/downloading states here.
    for candidate in (RequestStatus.SEARCHING, *ACTIVE_STAGING_WORKFLOW_STATUSES[::-1]):
        if candidate in statuses:
            return candidate

    # ── Terminal state detection ─────────────────────────────────
    if statuses == {RequestStatus.COMPLETED}:
        return RequestStatus.COMPLETED

    # Mixed completed with true in-progress work → PENDING, but completed aired
    # episodes plus only future/unreleased episodes means no current search work.
    if RequestStatus.COMPLETED in statuses:
        non_terminal = statuses - {
            RequestStatus.COMPLETED,
            RequestStatus.FAILED,
            RequestStatus.DENIED,
            RequestStatus.UNRELEASED,
        }
        if non_terminal:
            return RequestStatus.PENDING

        if RequestStatus.UNRELEASED in statuses:
            return RequestStatus.UNRELEASED

    # Any PENDING episode → treat as in-progress
    if RequestStatus.PENDING in statuses:
        return RequestStatus.PENDING

    if RequestStatus.FAILED in statuses:
        return RequestStatus.FAILED
    if RequestStatus.DENIED in statuses:
        return RequestStatus.DENIED

    # Only unreleased episodes remain
    if RequestStatus.UNRELEASED in statuses or episodes_are_unreleased(episodes):
        return RequestStatus.UNRELEASED

    return RequestStatus.PENDING


def derive_request_status_from_episodes(episodes: Sequence[Episode]) -> RequestStatus:
    """Derive aggregate TV request status from episode statuses."""
    if not episodes:
        return RequestStatus.PENDING
    statuses = {ep.status for ep in episodes}
    if statuses == {RequestStatus.COMPLETED}:
        return RequestStatus.COMPLETED
    if statuses == {RequestStatus.DOWNLOADING}:
        return RequestStatus.DOWNLOADING
    if statuses == {RequestStatus.STAGED}:
        return RequestStatus.STAGED
    if statuses == {RequestStatus.SEARCHING}:
        return RequestStatus.SEARCHING
    if RequestStatus.PENDING in statuses:
        return RequestStatus.PENDING
    if RequestStatus.COMPLETED in statuses:
        if statuses <= {RequestStatus.COMPLETED, RequestStatus.UNRELEASED}:
            return RequestStatus.UNRELEASED
        return RequestStatus.PENDING
    return derive_season_status(episodes)


def apply_synced_request_status(request: Request, episodes: Sequence[Episode]) -> None:
    """Set a TV request's status from synced episodes, keeping DENIED requests denied.

    Episode syncs add new episodes as PENDING. Without this guard, a sync would
    move a denied request back into the pending queue.
    """
    if request.status == RequestStatus.DENIED:
        return
    request.status = derive_request_status_from_episodes(episodes)


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
    searching = sum(1 for ep in episodes if ep.status == RequestStatus.SEARCHING)
    pending = sum(1 for ep in episodes if ep.status == RequestStatus.PENDING)

    if completed == total:
        return "completed"
    active = downloading + staged + searching
    if active and active < total:
        return "partial"
    if downloading:
        return "downloading"
    if staged:
        return "staged"
    if searching:
        return "searching"
    if pending == total:
        return "pending"
    if completed > 0:
        return "partial"

    return "pending"
