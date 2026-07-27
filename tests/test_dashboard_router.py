from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.siftarr.models import StagedTorrent
from app.siftarr.models.request import MediaType, RequestStatus
from app.siftarr.routers import dashboard
from app.siftarr.routers.dashboard import (
    _is_actionable_workflow_torrent,
    _match_qbit_torrents,
    _serialize_qbit_download,
)


def test_staged_torrent_remains_actionable_when_tv_request_aggregate_is_pending():
    torrent = cast(StagedTorrent, SimpleNamespace(request_id=42, status="staged"))

    assert _is_actionable_workflow_torrent(torrent, {42: RequestStatus.PENDING})


def test_approved_torrent_requires_active_request_status():
    torrent = cast(StagedTorrent, SimpleNamespace(request_id=42, status="approved"))

    assert not _is_actionable_workflow_torrent(torrent, {42: RequestStatus.COMPLETED})
    assert _is_actionable_workflow_torrent(torrent, {42: RequestStatus.DOWNLOADING})


def test_qbit_torrents_include_unmanaged_and_match_unique_managed_hash():
    managed = cast(
        StagedTorrent,
        SimpleNamespace(id=7, title="Managed.Release", info_hash="ABC123"),
    )

    rows = _match_qbit_torrents(
        [{"hash": "abc123", "name": "renamed"}, {"hash": "other", "name": "Manual"}],
        [managed],
    )

    assert rows[0]["managed_torrent"] is managed
    assert rows[1]["managed_torrent"] is None


def test_qbit_download_payload_only_exposes_actions_for_managed_match():
    managed = SimpleNamespace(id=7, request_id=42, move_status=None, moved_path=None)

    row = _serialize_qbit_download(
        {"hash": "abc123", "name": "Managed", "progress": 0.5, "managed_torrent": managed}
    )
    unmanaged = _serialize_qbit_download({"hash": "other", "name": "Manual"})

    assert row["managed"] == {
        "id": 7,
        "request_id": 42,
        "move_status": "pending",
        "moved_path": None,
    }
    assert unmanaged["managed"] is None


@pytest.mark.asyncio
async def test_downloads_api_refreshes_unmanaged_queue_and_authorized_matches(monkeypatch):
    managed = SimpleNamespace(
        id=7,
        request_id=42,
        status="approved",
        info_hash="abc123",
        title="Managed Release",
        move_status="pending",
        moved_path=None,
    )
    qbit_service = SimpleNamespace(
        get_unfinished_torrents_or_raise=AsyncMock(
            return_value=[
                {"hash": "abc123", "name": "Managed Release", "progress": 0.5},
                {"hash": "manual", "name": "Manual", "progress": 0.25},
            ]
        )
    )
    db = MagicMock()
    db.execute = AsyncMock(
        side_effect=[
            MagicMock(
                scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[managed])))
            ),
            MagicMock(all=MagicMock(return_value=[(42, RequestStatus.DOWNLOADING)])),
            MagicMock(all=MagicMock(return_value=[(42, "Managed Show", MediaType.TV)])),
        ]
    )
    monkeypatch.setattr(
        dashboard, "get_settings", lambda: SimpleNamespace(qbittorrent_url="http://qb")
    )
    monkeypatch.setattr(dashboard, "QbittorrentService", lambda settings: qbit_service)

    payload = await dashboard.qbit_downloads_api(db=db)

    assert payload["qbit_unavailable"] is False
    groups = payload["groups"]
    assert [group["title"] for group in groups] == ["Managed Show", "Unmanaged"]
    assert groups[0]["torrents"][0]["managed"]["id"] == 7
    assert groups[1]["unmanaged"] is True
    assert groups[1]["torrents"][0]["managed"] is None


def _downloads_api_db(managed, request_status):
    db = MagicMock()
    db.execute = AsyncMock(
        side_effect=[
            MagicMock(
                scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[managed])))
            ),
            MagicMock(all=MagicMock(return_value=[(managed.request_id, request_status)])),
            MagicMock(
                all=MagicMock(return_value=[(managed.request_id, "Managed Show", MediaType.TV)])
            ),
        ]
    )
    return db


def _patch_downloads_api_qbit(monkeypatch):
    qbit_service = SimpleNamespace(
        get_unfinished_torrents_or_raise=AsyncMock(
            return_value=[{"hash": "abc123", "name": "Managed Release", "progress": 0.5}]
        )
    )
    monkeypatch.setattr(
        dashboard, "get_settings", lambda: SimpleNamespace(qbittorrent_url="http://qb")
    )
    monkeypatch.setattr(dashboard, "QbittorrentService", lambda settings: qbit_service)


def _approved_torrent():
    return SimpleNamespace(
        id=7,
        request_id=42,
        status="approved",
        info_hash="abc123",
        title="Managed Release",
        move_status="pending",
        moved_path=None,
    )


@pytest.mark.asyncio
async def test_downloads_api_keeps_approved_torrent_when_tv_request_derives_pending(monkeypatch):
    """A single-episode approval derives the TV request back to PENDING, but the
    approved torrent is still downloading and must stay visible."""
    _patch_downloads_api_qbit(monkeypatch)
    managed = _approved_torrent()

    payload = await dashboard.qbit_downloads_api(
        db=_downloads_api_db(managed, RequestStatus.PENDING)
    )

    group = payload["groups"][0]
    assert group["request_id"] == 42
    assert group["title"] == "Managed Show"
    assert group["media_type"] == "tv"
    assert group["torrents"][0]["managed"] == {
        "id": 7,
        "request_id": 42,
        "move_status": "pending",
        "moved_path": None,
    }


@pytest.mark.asyncio
async def test_downloads_api_drops_approved_torrent_for_terminal_request(monkeypatch):
    _patch_downloads_api_qbit(monkeypatch)
    managed = _approved_torrent()

    payload = await dashboard.qbit_downloads_api(
        db=_downloads_api_db(managed, RequestStatus.COMPLETED)
    )

    group = payload["groups"][0]
    assert group["unmanaged"] is True
    assert group["torrents"][0]["managed"] is None
