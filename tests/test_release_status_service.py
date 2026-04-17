"""Tests for the release-status classifier pure functions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from app.siftarr.models.request import RequestStatus
from app.siftarr.services.release_status_service import (
    _parse_date,
    classify_movie,
    classify_tv_request,
)

TODAY = date(2026, 4, 17)
YESTERDAY = date(2026, 4, 16)
TOMORROW = date(2026, 4, 18)
NEXT_YEAR = date(2027, 4, 17)
LAST_YEAR = date(2025, 4, 17)


@dataclass
class FakeEpisode:
    air_date: date | None
    status: RequestStatus


# ---------------------------------------------------------------------------
# _parse_date
# ---------------------------------------------------------------------------


def test_parse_date_handles_none_and_empty():
    assert _parse_date(None) is None
    assert _parse_date("") is None


def test_parse_date_iso_date():
    assert _parse_date("2026-04-17") == date(2026, 4, 17)


def test_parse_date_iso_datetime_falls_back_to_date():
    assert _parse_date("2026-04-17T12:34:56") == date(2026, 4, 17)


def test_parse_date_unparseable_returns_none():
    assert _parse_date("not-a-date") is None


# ---------------------------------------------------------------------------
# classify_movie
# ---------------------------------------------------------------------------


def test_movie_none_details_is_released_fail_open():
    assert classify_movie(None, today=TODAY) == "released"


def test_movie_released_status_with_past_date_is_released():
    details = {"status": "Released", "releaseDate": "2025-01-01"}
    assert classify_movie(details, today=TODAY) == "released"


def test_movie_future_release_is_unreleased():
    details = {
        "status": "Post Production",
        "releaseDate": NEXT_YEAR.isoformat(),
        "releases": {"results": []},
    }
    assert classify_movie(details, today=TODAY) == "unreleased"


def test_movie_missing_release_date_and_not_released_is_unreleased():
    details = {"status": "Planned"}
    assert classify_movie(details, today=TODAY) == "unreleased"


def test_movie_post_production_but_past_digital_release_is_released():
    # Status is not "Released" and primary releaseDate is future, BUT a
    # digital release date (type=4) is already in the past → released.
    details = {
        "status": "Post Production",
        "releaseDate": NEXT_YEAR.isoformat(),
        "releases": {
            "results": [
                {
                    "iso_3166_1": "US",
                    "release_dates": [
                        {"type": 4, "release_date": YESTERDAY.isoformat()},
                    ],
                }
            ]
        },
    }
    assert classify_movie(details, today=TODAY) == "released"


def test_movie_past_premiere_type_only_still_unreleased():
    # type=1 (Premiere) and type=2 (Theatrical limited) are not in {3,4,5}.
    details = {
        "status": "Post Production",
        "releaseDate": NEXT_YEAR.isoformat(),
        "releases": {
            "results": [
                {
                    "release_dates": [
                        {"type": 1, "release_date": LAST_YEAR.isoformat()},
                        {"type": 2, "release_date": LAST_YEAR.isoformat()},
                    ],
                }
            ]
        },
    }
    assert classify_movie(details, today=TODAY) == "unreleased"


def test_movie_malformed_releases_block_is_tolerated():
    details = {
        "status": "Planned",
        "releaseDate": None,
        "releases": "garbage",
    }
    assert classify_movie(details, today=TODAY) == "unreleased"


def test_movie_released_status_overrides_even_with_missing_date():
    # Status == "Released" → first condition fails → released.
    details = {"status": "Released", "releaseDate": None}
    assert classify_movie(details, today=TODAY) == "released"


def test_movie_release_date_today_is_released():
    details = {"status": "Released", "releaseDate": TODAY.isoformat()}
    assert classify_movie(details, today=TODAY) == "released"


# ---------------------------------------------------------------------------
# classify_tv_request
# ---------------------------------------------------------------------------


def test_tv_none_details_is_released_fail_open():
    assert classify_tv_request(None, [], today=TODAY) == "released"


def test_tv_fully_unaired_missing_first_air_date():
    details = {"firstAirDate": None, "status": "Planned"}
    assert classify_tv_request(details, [], today=TODAY) == "unreleased"


def test_tv_fully_unaired_future_first_air_date():
    details = {"firstAirDate": NEXT_YEAR.isoformat(), "status": "Returning Series"}
    assert classify_tv_request(details, [], today=TODAY) == "unreleased"


def test_tv_status_in_production_no_local_aired_is_unreleased():
    details = {"firstAirDate": LAST_YEAR.isoformat(), "status": "In Production"}
    episodes = [FakeEpisode(air_date=TOMORROW, status=RequestStatus.RECEIVED)]
    assert classify_tv_request(details, episodes, today=TODAY) == "unreleased"


def test_tv_all_aired_downloaded_with_future_remaining_is_unreleased():
    details = {"firstAirDate": LAST_YEAR.isoformat(), "status": "Returning Series"}
    episodes = [
        FakeEpisode(air_date=LAST_YEAR, status=RequestStatus.AVAILABLE),
        FakeEpisode(air_date=YESTERDAY, status=RequestStatus.COMPLETED),
        FakeEpisode(air_date=NEXT_YEAR, status=RequestStatus.RECEIVED),
    ]
    assert classify_tv_request(details, episodes, today=TODAY) == "unreleased"


def test_tv_all_aired_downloaded_with_unknown_airdate_remaining_is_unreleased():
    details = {"firstAirDate": LAST_YEAR.isoformat(), "status": "Returning Series"}
    episodes = [
        FakeEpisode(air_date=YESTERDAY, status=RequestStatus.COMPLETED),
        FakeEpisode(air_date=None, status=RequestStatus.RECEIVED),
    ]
    assert classify_tv_request(details, episodes, today=TODAY) == "unreleased"


def test_tv_one_aired_episode_still_received_is_released():
    details = {"firstAirDate": LAST_YEAR.isoformat(), "status": "Returning Series"}
    episodes = [
        FakeEpisode(air_date=YESTERDAY, status=RequestStatus.RECEIVED),
        FakeEpisode(air_date=NEXT_YEAR, status=RequestStatus.RECEIVED),
    ]
    assert classify_tv_request(details, episodes, today=TODAY) == "released"


def test_tv_all_aired_downloaded_and_nothing_future_is_released():
    # No future/unknown episodes remain → condition (2) fails, fall through
    # to (3) which returns "released".
    details = {"firstAirDate": LAST_YEAR.isoformat(), "status": "Ended"}
    episodes = [
        FakeEpisode(air_date=LAST_YEAR, status=RequestStatus.AVAILABLE),
        FakeEpisode(air_date=YESTERDAY, status=RequestStatus.COMPLETED),
    ]
    assert classify_tv_request(details, episodes, today=TODAY) == "released"


def test_tv_empty_local_episodes_with_past_first_air_is_released():
    details = {"firstAirDate": LAST_YEAR.isoformat(), "status": "Returning Series"}
    assert classify_tv_request(details, [], today=TODAY) == "released"


def test_tv_future_first_air_but_aired_local_episode_is_released():
    # Overseerr says future, but we somehow already have an aired episode:
    # condition (1) requires no aired-locally, so we fall to (2)/(3).
    details = {"firstAirDate": NEXT_YEAR.isoformat(), "status": "Planned"}
    episodes = [
        FakeEpisode(air_date=YESTERDAY, status=RequestStatus.RECEIVED),
    ]
    assert classify_tv_request(details, episodes, today=TODAY) == "released"


def test_tv_air_date_exactly_today_counts_as_aired():
    details = {"firstAirDate": LAST_YEAR.isoformat(), "status": "Returning Series"}
    episodes = [
        FakeEpisode(air_date=TODAY, status=RequestStatus.AVAILABLE),
        FakeEpisode(air_date=TOMORROW, status=RequestStatus.RECEIVED),
    ]
    assert classify_tv_request(details, episodes, today=TODAY) == "unreleased"
