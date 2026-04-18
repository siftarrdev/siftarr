"""Release-status classifier (pure functions)."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime
from typing import Literal, Protocol

from app.siftarr.models.request import RequestStatus

_RELEASE_TYPES_AVAILABLE = {3, 4, 5}
_TV_UNAIRED_STATUSES = {"Planned", "In Production", "Pilot"}
_AVAILABLE_EPISODE_STATUSES = {RequestStatus.AVAILABLE, RequestStatus.COMPLETED}


class EpisodeLike(Protocol):
    air_date: date | None
    status: RequestStatus


def _parse_date(value: str | None) -> date | None:
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
    if details is None:
        return "released"

    today = today or date.today()
    status = details.get("status")
    status_not_released = status != "Released"
    release_date = _parse_date(details.get("releaseDate"))
    release_date_missing_or_future = release_date is None or release_date > today

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
                    parsed = _parse_date(entry.get("release_date"))
                    if parsed is not None and parsed <= today:
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
    if tv_details is None:
        return "released"

    today = today or date.today()
    episodes = list(local_episodes)

    any_aired_locally = any(e.air_date is not None and e.air_date <= today for e in episodes)
    first_air = _parse_date(tv_details.get("firstAirDate"))
    first_air_missing_or_future = first_air is None or first_air > today
    series_status = tv_details.get("status")
    series_status_unaired = series_status in _TV_UNAIRED_STATUSES

    if (first_air_missing_or_future or series_status_unaired) and not any_aired_locally:
        return "unreleased"

    if any_aired_locally:
        aired = [e for e in episodes if e.air_date is not None and e.air_date <= today]
        all_aired_downloaded = all(e.status in _AVAILABLE_EPISODE_STATUSES for e in aired)
        has_future_or_unknown = any(e.air_date is None or e.air_date > today for e in episodes)
        if all_aired_downloaded and has_future_or_unknown:
            return "unreleased"
        return "released"

    return "released"
