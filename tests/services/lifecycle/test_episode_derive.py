from datetime import date
from types import SimpleNamespace

import pytest

from app.siftarr.models.request import (
    ACTIVE_STAGING_WORKFLOW_STATUSES,
    AVAILABILITY_SAFE_REQUEST_STATUSES,
    MUTABLE_REQUEST_STATUSES,
    NON_TERMINAL_REQUEST_STATUSES,
    RESETTABLE_EPISODE_DOWNLOAD_STATUSES,
    TERMINAL_REQUEST_STATUSES,
    RequestStatus,
    is_active_staging_workflow_status,
    is_mutable_request_status,
    is_terminal_request_status,
)
from app.siftarr.services.lifecycle.episode_derive import (
    derive_episode_status,
    derive_request_status_from_episodes,
    derive_season_status,
    derive_tv_display_label,
)

S = RequestStatus


def _ep(status: RequestStatus):
    return SimpleNamespace(status=status)


@pytest.mark.parametrize(
    ("on_plex", "air_date", "expected"),
    [
        pytest.param(True, date.max, S.COMPLETED, id="plex-wins-over-future"),
        pytest.param(False, date.max, S.UNRELEASED, id="future-unreleased"),
        pytest.param(False, date(2024, 1, 1), S.PENDING, id="aired-pending"),
    ],
)
def test_derive_episode_status(on_plex, air_date, expected):
    assert derive_episode_status(is_on_plex=on_plex, air_date=air_date) == expected


@pytest.mark.parametrize(
    ("statuses", "request_status", "display_label"),
    [
        pytest.param([S.COMPLETED], S.COMPLETED, None, id="all-completed"),
        pytest.param([S.UNRELEASED], S.UNRELEASED, None, id="only-unreleased"),
        pytest.param([S.COMPLETED, S.UNRELEASED], S.UNRELEASED, None, id="completed-and-future"),
        pytest.param([S.PENDING, S.UNRELEASED], S.PENDING, None, id="pending-and-future"),
        pytest.param([S.DOWNLOADING, S.PENDING], S.PENDING, "partial", id="downloading-mixed"),
        pytest.param(
            [S.DOWNLOADING, S.DOWNLOADING], S.DOWNLOADING, "downloading", id="downloading"
        ),
        pytest.param([S.SEARCHING, S.PENDING], None, "partial", id="searching-mixed"),
    ],
)
def test_episode_rollups(statuses, request_status, display_label):
    episodes = [_ep(status) for status in statuses]

    if request_status is not None:
        assert derive_request_status_from_episodes(episodes) == request_status
    if display_label is not None:
        assert derive_tv_display_label(episodes) == display_label


def test_season_status_with_completed_and_unreleased_episodes_is_unreleased():
    assert derive_season_status([_ep(S.COMPLETED), _ep(S.UNRELEASED)]) == S.UNRELEASED


def test_lifecycle_status_sets_preserve_existing_groupings() -> None:
    assert ACTIVE_STAGING_WORKFLOW_STATUSES == (S.STAGED, S.DOWNLOADING)
    assert NON_TERMINAL_REQUEST_STATUSES == (
        S.SEARCHING,
        S.PENDING,
        S.UNRELEASED,
        S.STAGED,
        S.DOWNLOADING,
    )
    assert TERMINAL_REQUEST_STATUSES == (S.COMPLETED, S.FAILED, S.DENIED)
    assert MUTABLE_REQUEST_STATUSES == (
        S.SEARCHING,
        S.PENDING,
        S.UNRELEASED,
        S.STAGED,
        S.DOWNLOADING,
        S.FAILED,
    )
    assert RESETTABLE_EPISODE_DOWNLOAD_STATUSES == (S.DOWNLOADING, S.STAGED, S.SEARCHING)
    assert AVAILABILITY_SAFE_REQUEST_STATUSES == (S.UNRELEASED, S.COMPLETED)


def test_lifecycle_status_predicates_accept_enum_and_string() -> None:
    assert is_active_staging_workflow_status(S.STAGED)
    assert is_active_staging_workflow_status("downloading")
    assert is_terminal_request_status("completed")
    assert is_mutable_request_status(S.FAILED)
    assert not is_mutable_request_status(S.DENIED)
    assert not is_terminal_request_status("unknown")
