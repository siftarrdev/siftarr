from types import SimpleNamespace
from typing import cast

from app.siftarr.models import StagedTorrent
from app.siftarr.models.request import RequestStatus
from app.siftarr.routers.dashboard import _is_actionable_workflow_torrent


def test_staged_torrent_remains_actionable_when_tv_request_aggregate_is_pending():
    torrent = cast(StagedTorrent, SimpleNamespace(request_id=42, status="staged"))

    assert _is_actionable_workflow_torrent(torrent, {42: RequestStatus.PENDING})


def test_approved_torrent_requires_active_request_status():
    torrent = cast(StagedTorrent, SimpleNamespace(request_id=42, status="approved"))

    assert not _is_actionable_workflow_torrent(torrent, {42: RequestStatus.COMPLETED})
    assert _is_actionable_workflow_torrent(torrent, {42: RequestStatus.DOWNLOADING})
