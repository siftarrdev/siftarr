from types import SimpleNamespace

from app.siftarr.models.request import RequestStatus
from app.siftarr.services.lifecycle.episode_derive import (
    derive_request_status_from_episodes,
    derive_tv_display_label,
)


def _ep(status: RequestStatus):
    return SimpleNamespace(status=status)


def test_mixed_downloading_tv_request_is_pending_aggregate_and_partial_display():
    episodes = [_ep(RequestStatus.DOWNLOADING), _ep(RequestStatus.PENDING)]

    assert derive_request_status_from_episodes(episodes) == RequestStatus.PENDING
    assert derive_tv_display_label(episodes) == "partial"


def test_all_downloading_tv_request_remains_downloading():
    episodes = [_ep(RequestStatus.DOWNLOADING), _ep(RequestStatus.DOWNLOADING)]

    assert derive_request_status_from_episodes(episodes) == RequestStatus.DOWNLOADING
    assert derive_tv_display_label(episodes) == "downloading"


def test_mixed_searching_tv_display_is_partial():
    episodes = [_ep(RequestStatus.SEARCHING), _ep(RequestStatus.PENDING)]

    assert derive_tv_display_label(episodes) == "partial"
