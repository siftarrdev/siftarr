from unittest.mock import AsyncMock

import pytest

from app.siftarr.models.request import RequestStatus
from app.siftarr.services.integrations.plex_service import (
    PlexEpisodeAvailabilityResult,
    PlexLookupResult,
    PlexTransientScanError,
)
from tests.factories import make_request, make_staged_torrent

from .helpers import (
    episode_statuses,
    make_tv_request,
    recent_item,
    request_status,
    set_recent_items,
)

COMPLETED = RequestStatus.COMPLETED
PENDING = RequestStatus.PENDING
DOWNLOADING = RequestStatus.DOWNLOADING


async def test_scan_recent_completes_matches_and_skips_the_rest(db, service, mock_plex):
    movie = await make_request(db, external_id="a", tmdb_id=111)
    duplicate = await make_request(db, external_id="b", tmdb_id=111, title="Duplicate")
    lookup_misses = await make_request(db, external_id="c", tmdb_id=222)
    lookup_hits = await make_request(db, external_id="d", tmdb_id=333)
    not_recent = await make_request(db, external_id="e", tmdb_id=999)
    show = await make_tv_request(db, external_id="f", tmdb_id=444)
    await db.commit()

    set_recent_items(
        mock_plex,
        movies=[
            recent_item("movie", 111, minute=0),
            recent_item("movie", 222, has_media=False, minute=1),
            recent_item("movie", 333, has_media=False, minute=2),
            recent_item("movie", 555, minute=3),
        ],
        shows=[recent_item("show", 444, minute=4)],
    )

    async def lookup_movie(tmdb_id: int) -> PlexLookupResult:
        item = {"rating_key": "movie-333", "Media": [{"id": 1}]} if tmdb_id == 333 else None
        return PlexLookupResult(item=item, authoritative=True)

    mock_plex.lookup_movie_by_tmdb = AsyncMock(side_effect=lookup_movie)
    mock_plex.get_episode_availability_result = AsyncMock(
        return_value=PlexEpisodeAvailabilityResult(
            availability={(1, 1): True, (1, 2): True}, authoritative=True
        )
    )

    events: list[dict] = []
    result = await service.scan_recent(on_progress=events.append)

    assert events
    assert result.completed_requests == 4
    assert result.metrics.scanned_items == 5
    assert result.metrics.matched_requests == 4
    assert result.metrics.skipped_on_error_items == 0
    assert result.clean_run is True
    assert await request_status(db, movie) == COMPLETED
    assert await request_status(db, duplicate) == COMPLETED
    assert await request_status(db, lookup_hits) == COMPLETED
    assert await request_status(db, show) == COMPLETED
    assert await request_status(db, lookup_misses) == PENDING
    assert await request_status(db, not_recent) == PENDING
    # Items with media skip the per-title Plex lookup; only media-less items fall back to it.
    assert sorted(call.args[0] for call in mock_plex.lookup_movie_by_tmdb.await_args_list) == [
        222,
        333,
    ]
    mock_plex.check_movie_available.assert_not_called()
    mock_plex.get_show_by_tmdb.assert_not_called()


async def test_scan_recent_reports_recent_listing_failure(db, service, mock_plex):
    req = await make_request(db, tmdb_id=111)
    await db.commit()
    set_recent_items(mock_plex, movie_error=PlexTransientScanError("recently added unavailable"))

    result = await service.scan_recent()

    assert result.completed_requests == 0
    assert result.metrics.scanned_items == 0
    assert result.metrics.skipped_on_error_items == 1
    assert result.clean_run is False
    assert result.last_error == "recently added unavailable"
    assert await request_status(db, req) == PENDING


def _movie_lookup_raises(plex):
    set_recent_items(plex, movies=[recent_item("movie", 111, has_media=False)])
    plex.lookup_movie_by_tmdb = AsyncMock(side_effect=Exception("boom"))


def _movie_lookup_not_authoritative(plex):
    set_recent_items(plex, movies=[recent_item("movie", 111, has_media=False)])
    plex.lookup_movie_by_tmdb = AsyncMock(
        return_value=PlexLookupResult(item=None, authoritative=False)
    )


def _tv_availability_not_authoritative(plex):
    set_recent_items(plex, shows=[recent_item("show", 111, has_media=False, rating_key=False)])
    plex.lookup_show_by_tmdb = AsyncMock(
        return_value=PlexLookupResult(
            item={"rating_key": "show-111", "Media": []}, authoritative=True
        )
    )
    plex.get_episode_availability_result = AsyncMock(
        return_value=PlexEpisodeAvailabilityResult(availability={}, authoritative=False)
    )


@pytest.mark.parametrize(
    ("arrange", "is_tv", "expected_error"),
    [
        pytest.param(
            _movie_lookup_raises,
            False,
            "Recent Plex scan had request probe errors",
            id="movie_lookup_raises",
        ),
        pytest.param(_movie_lookup_not_authoritative, False, None, id="movie_not_authoritative"),
        pytest.param(_tv_availability_not_authoritative, True, None, id="tv_not_authoritative"),
    ],
)
async def test_scan_recent_flags_unreliable_lookups_without_completing(
    db, service, mock_plex, arrange, is_tv, expected_error
):
    req = await (
        make_tv_request(db, tmdb_id=111, seasons={1: 1}) if is_tv else make_request(db, tmdb_id=111)
    )
    await db.commit()
    arrange(mock_plex)

    result = await service.scan_recent()

    assert result.completed_requests == 0
    assert result.metrics.scanned_items == 1
    assert result.metrics.matched_requests == 0
    assert result.metrics.skipped_on_error_items == 1
    assert result.clean_run is False
    if expected_error:
        assert result.last_error == expected_error
    assert await request_status(db, req) == PENDING
    if is_tv:
        assert set((await episode_statuses(db, req)).values()) == {PENDING}


@pytest.mark.parametrize(
    ("plex_check", "expected_status", "expected_matches", "expected_errors", "expected_error"),
    [
        pytest.param(True, COMPLETED, 1, 0, None, id="available_completes"),
        pytest.param(
            RuntimeError("plex down"),
            DOWNLOADING,
            0,
            1,
            "Targeted Plex availability checks had probe errors",
            id="probe_error_reported",
        ),
    ],
)
async def test_scan_recent_targeted_check_for_approved_download_absent_from_recent(
    db,
    service,
    mock_plex,
    plex_check,
    expected_status,
    expected_matches,
    expected_errors,
    expected_error,
):
    req = await make_request(db, status=DOWNLOADING, tmdb_id=5050)
    await make_staged_torrent(db, req, status="approved")
    await db.commit()
    set_recent_items(mock_plex)
    mock_plex.check_movie_available = AsyncMock(
        side_effect=plex_check if isinstance(plex_check, Exception) else None,
        return_value=plex_check,
    )

    result = await service.scan_recent()

    assert result.completed_requests == expected_matches
    assert result.metrics.scanned_items == 0
    assert result.metrics.recent_item_matches == 0
    assert result.metrics.targeted_checks == 1
    assert result.metrics.targeted_matches == expected_matches
    assert result.metrics.targeted_probe_errors == expected_errors
    assert result.metrics.skipped_on_error_items == expected_errors
    assert result.last_error == expected_error
    assert result.clean_run is (expected_error is None)
    assert await request_status(db, req) == expected_status
