"""Release-status classifier (pure functions).

Classifies movie and TV requests as `released` / `unreleased` (and `partial`
for TV) based on Overseerr detail payloads and, for TV, locally-known episode
rows. Module is dependency-free (stdlib + `RequestStatus`) and side-effect
free: no HTTP, no DB, no logging beyond debug.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import date, datetime
from typing import Literal, Protocol

from app.siftarr.models.request import RequestStatus

__all__ = [
    "EpisodeLike",
    "classify_movie",
    "classify_tv_request",
]

_logger = logging.getLogger(__name__)

# Overseerr release-date types considered "actually watchable / grabbable":
# 3 = Theatrical, 4 = Digital, 5 = Physical.
_RELEASE_TYPES_AVAILABLE = {3, 4, 5}

# Series statuses that imply the show has not started airing yet.
_TV_UNAIRED_STATUSES = {"Planned", "In Production", "Pilot"}

_AVAILABLE_EPISODE_STATUSES = {RequestStatus.AVAILABLE, RequestStatus.COMPLETED}


class EpisodeLike(Protocol):
    """Minimal protocol for episode rows used by the TV classifier.

    Tests can pass any object (e.g. a `dataclass` or `SimpleNamespace`) with
    these attributes; the real `Episode` ORM model satisfies it naturally.
    """

    air_date: date | None
    status: RequestStatus


def _parse_date(value: str | None) -> date | None:
    """Parse an ISO-8601 date (or datetime) string into a `date`.

    Returns `None` for `None`, empty string, or unparseable input. Accepts
    plain `YYYY-MM-DD` as well as full ISO datetimes (falls back to the date
    component).
    """
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        pass
    try:
        return datetime.fromisoformat(value).date()
    except ValueError:
        return None


def classify_movie(
    details: dict | None,
    *,
    today: date | None = None,
) -> Literal["released", "unreleased"]:
    """Classify a movie as `released` or `unreleased`.

    See the plan's "Classification Rules" for the full semantics. Fail-open:
    `details is None` → `"released"`.
    """
    if details is None:
        return "released"

    today = today or date.today()

    status = details.get("status")
    status_not_released = status != "Released"

    release_date_raw = details.get("releaseDate")
    release_date = _parse_date(release_date_raw)
    release_date_missing_or_future = release_date is None or release_date > today

    # Scan Overseerr's per-country release_dates list for any type 3/4/5 date
    # that is already on/before today.
    has_past_avail_release = False
    releases_block = details.get("releases")
    if isinstance(releases_block, dict):
        results = releases_block.get("results")
        if isinstance(results, list):
            for country in results:
                if not isinstance(country, dict):
                    continue
                dates = country.get("release_dates")
                if not isinstance(dates, list):
                    continue
                for entry in dates:
                    if not isinstance(entry, dict):
                        continue
                    if entry.get("type") not in _RELEASE_TYPES_AVAILABLE:
                        continue
                    d = _parse_date(entry.get("release_date"))
                    if d is not None and d <= today:
                        has_past_avail_release = True
                        break
                if has_past_avail_release:
                    break

    if status_not_released and release_date_missing_or_future and not has_past_avail_release:
        return "unreleased"
    return "released"


def classify_tv_request(
    tv_details: dict | None,
    local_episodes: Iterable[EpisodeLike],
    *,
    today: date | None = None,
) -> Literal["released", "partial", "unreleased"]:
    """Classify a TV request as `released`, `partial`, or `unreleased`.

    `"partial"` is reserved for future refinement; the evaluator today treats
    it identically to `"released"`.
    """
    if tv_details is None:
        return "released"

    today = today or date.today()
    episodes = list(local_episodes)

    any_aired_locally = any(e.air_date is not None and e.air_date <= today for e in episodes)

    first_air_raw = tv_details.get("firstAirDate")
    first_air = _parse_date(first_air_raw)
    first_air_missing_or_future = first_air is None or first_air > today

    series_status = tv_details.get("status")
    series_status_unaired = series_status in _TV_UNAIRED_STATUSES

    # 1. Fully unaired series.
    if (first_air_missing_or_future or series_status_unaired) and not any_aired_locally:
        return "unreleased"

    # 2. Partial - all aired episodes already local, with future episodes remaining.
    if any_aired_locally:
        aired = [e for e in episodes if e.air_date is not None and e.air_date <= today]
        all_aired_downloaded = all(e.status in _AVAILABLE_EPISODE_STATUSES for e in aired)
        has_future_or_unknown = any(e.air_date is None or e.air_date > today for e in episodes)
        if all_aired_downloaded and has_future_or_unknown:
            return "unreleased"
        # 3. Any aired episode not yet local.
        return "released"

    # 4. Fallback: no local episodes and the series has aired.
    # (first_air_missing_or_future=False and not series_status_unaired here.)
    return "released"
