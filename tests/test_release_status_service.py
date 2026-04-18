from datetime import date
from types import SimpleNamespace

from app.siftarr.models.request import RequestStatus
from app.siftarr.services.release_status_service import classify_movie, classify_tv_request

TODAY = date(2026, 4, 17)


def test_classify_movie_future_release_is_unreleased():
    details = {
        "status": "In Production",
        "releaseDate": "2026-08-01",
        "releases": {"results": []},
    }
    assert classify_movie(details, today=TODAY) == "unreleased"


def test_tv_all_aired_downloaded_with_future_remaining_is_unreleased():
    details = {
        "firstAirDate": "2025-01-01",
        "status": "Returning Series",
    }
    episodes = [
        SimpleNamespace(air_date=date(2026, 4, 1), status=RequestStatus.AVAILABLE),
        SimpleNamespace(air_date=date(2026, 4, 8), status=RequestStatus.AVAILABLE),
        SimpleNamespace(air_date=date(2026, 5, 1), status=RequestStatus.UNRELEASED),
    ]
    assert classify_tv_request(details, episodes, today=TODAY) == "unreleased"
