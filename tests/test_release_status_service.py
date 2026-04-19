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


def test_tv_all_aired_downloaded_with_empty_season_is_unreleased():
    """A series with all aired episodes downloaded but an empty future season is unreleased."""
    details = {
        "firstAirDate": "2025-01-01",
        "status": "Returning Series",
    }
    episodes = [
        SimpleNamespace(air_date=date(2026, 4, 1), status=RequestStatus.AVAILABLE),
        SimpleNamespace(air_date=date(2026, 4, 8), status=RequestStatus.AVAILABLE),
    ]
    # has_empty_seasons=True simulates a Season record existing with no episodes
    assert (
        classify_tv_request(details, episodes, today=TODAY, has_empty_seasons=True) == "unreleased"
    )


def test_tv_all_aired_downloaded_no_empty_seasons_is_released():
    """A series with all aired episodes downloaded and no empty seasons is released."""
    details = {
        "firstAirDate": "2025-01-01",
        "status": "Returning Series",
    }
    episodes = [
        SimpleNamespace(air_date=date(2026, 4, 1), status=RequestStatus.AVAILABLE),
        SimpleNamespace(air_date=date(2026, 4, 8), status=RequestStatus.AVAILABLE),
    ]
    assert (
        classify_tv_request(details, episodes, today=TODAY, has_empty_seasons=False) == "released"
    )


def test_tv_completed_episodes_with_future_next_episode_signal_is_unreleased():
    details = {
        "firstAirDate": "2025-01-01",
        "status": "Returning Series",
        "nextEpisodeToAir": {"airDate": "2026-05-01"},
    }
    episodes = [
        SimpleNamespace(air_date=date(2026, 4, 1), status=RequestStatus.COMPLETED),
        SimpleNamespace(air_date=date(2026, 4, 8), status=RequestStatus.COMPLETED),
    ]

    assert classify_tv_request(details, episodes, today=TODAY) == "unreleased"
