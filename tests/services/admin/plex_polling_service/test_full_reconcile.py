import pytest

from app.siftarr.models.request import RequestStatus
from tests.factories import make_request

from .helpers import episode_statuses, make_tv_request, request_status

COMPLETED = RequestStatus.COMPLETED
PENDING = RequestStatus.PENDING
DOWNLOADING = RequestStatus.DOWNLOADING


async def test_check_request_movie_completes_match(db, service, mock_plex):
    req = await make_request(db, tmdb_id=111, title="Movie A")
    await db.commit()
    mock_plex.check_movie_available.return_value = True

    result = await service.check_request(req.id)

    assert (result.request_id, result.matched, result.available) == (req.id, True, True)
    assert await request_status(db, req) == COMPLETED


@pytest.mark.parametrize(
    ("seasons", "initial", "availability", "expected_request", "expected_reason", "expected_eps"),
    [
        pytest.param(
            {1: 2},
            PENDING,
            {(1, 1): True, (1, 2): True},
            COMPLETED,
            "All episodes found on Plex",
            {(1, 1): COMPLETED, (1, 2): COMPLETED},
            id="full_completes_request",
        ),
        pytest.param(
            {1: 2, 2: 2},
            DOWNLOADING,
            {(1, 1): True, (1, 2): True, (2, 1): False, (2, 2): False},
            PENDING,
            "Some episodes found on Plex",
            {
                (1, 1): COMPLETED,
                (1, 2): COMPLETED,
                (2, 1): DOWNLOADING,
                (2, 2): DOWNLOADING,
            },
            id="partial_season_pack_preserves_downloading_episodes",
        ),
    ],
)
async def test_check_request_tv(
    db,
    service,
    mock_plex,
    seasons,
    initial,
    availability,
    expected_request,
    expected_reason,
    expected_eps,
):
    req = await make_tv_request(db, seasons=seasons, status=initial, episode_status=initial)
    mock_plex.get_show_by_tmdb.return_value = {"rating_key": "42"}
    mock_plex.get_episode_availability.return_value = availability

    result = await service.check_request(req.id)

    assert result.matched is True
    assert result.available is True
    assert result.status_before == initial
    assert result.status_after == expected_request
    assert result.reason == expected_reason
    assert await request_status(db, req) == expected_request
    assert await episode_statuses(db, req) == expected_eps


async def test_targeted_completed_download_tv_filters_to_covered_episodes(db, service, mock_plex):
    req = await make_tv_request(db, status=DOWNLOADING, episode_status=DOWNLOADING)
    mock_plex.get_show_by_tmdb.return_value = {"rating_key": "42"}
    mock_plex.get_episode_availability.return_value = {(1, 1): True, (1, 2): True}

    result = await service.check_completed_download_waiting_for_plex(req.id, episode_keys={(1, 1)})

    assert result.matched is True
    assert result.status_after == PENDING
    assert await episode_statuses(db, req) == {(1, 1): COMPLETED, (1, 2): DOWNLOADING}


async def test_check_request_returns_unmatched_result_without_changing_state(
    db, service, mock_plex
):
    req = await make_request(db, tmdb_id=111)
    await db.commit()
    mock_plex.check_movie_available.return_value = False

    unmatched = await service.check_request(req.id)
    missing = await service.check_request(9999)

    assert (unmatched.matched, unmatched.status_after) == (False, PENDING)
    assert (missing.request_id, missing.matched) == (9999, False)
    assert await request_status(db, req) == PENDING
