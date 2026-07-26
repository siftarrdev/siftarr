"""Grouping of live qBittorrent torrents by owning Siftarr request."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.siftarr.models.request import MediaType, RequestStatus
from app.siftarr.routers import dashboard
from app.siftarr.services.dashboard.torrent_grouping import group_matched_torrents


def _managed(request_id, *, torrent_id=1, info_hash="hash", title="Managed"):
    return SimpleNamespace(
        id=torrent_id,
        request_id=request_id,
        status="approved",
        info_hash=info_hash,
        title=title,
        move_status="pending",
        moved_path=None,
    )


def test_torrents_sharing_a_request_collapse_into_one_group():
    managed = _managed(42)
    matched = [
        {"hash": "a", "name": "A", "dlspeed": 10, "size": 100, "managed_torrent": managed},
        {"hash": "b", "name": "B", "dlspeed": 5, "size": 50, "managed_torrent": managed},
    ]

    groups = group_matched_torrents(matched, {42: ("Show", MediaType.TV)})

    assert len(groups) == 1
    assert groups[0]["count"] == 2
    assert groups[0]["media_type"] == "tv"
    assert groups[0]["totals"]["dlspeed"] == 15
    assert groups[0]["totals"]["size"] == 150


def test_unmanaged_torrents_group_last():
    groups = group_matched_torrents(
        [
            {"hash": "u", "name": "Manual", "managed_torrent": None},
            {"hash": "z", "name": "Z", "managed_torrent": _managed(2)},
            {"hash": "a", "name": "A", "managed_torrent": _managed(1, torrent_id=2)},
            {"hash": "n", "name": "NoRequest", "managed_torrent": _managed(None, torrent_id=3)},
        ],
        {1: ("apple", MediaType.MOVIE), 2: ("Banana", MediaType.TV)},
    )

    assert [group["title"] for group in groups] == ["apple", "Banana", "Unmanaged"]
    assert groups[-1]["unmanaged"] is True
    assert groups[-1]["request_id"] is None
    assert groups[-1]["count"] == 2


def test_totals_treat_missing_and_none_values_as_zero():
    groups = group_matched_torrents(
        [
            {"hash": "a", "name": "A", "dlspeed": None, "managed_torrent": None},
            {"hash": "b", "name": "B", "uploaded": 7, "managed_torrent": None},
        ],
        {},
    )

    assert groups[0]["totals"] == {
        "dlspeed": 0,
        "upspeed": 0,
        "downloaded": 0,
        "uploaded": 7,
        "size": 0,
    }


def _db(managed, request_status, request_row):
    db = MagicMock()
    db.execute = AsyncMock(
        side_effect=[
            MagicMock(
                scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[managed])))
            ),
            MagicMock(all=MagicMock(return_value=[(managed.request_id, request_status)])),
            MagicMock(all=MagicMock(return_value=[request_row])),
        ]
    )
    return db


def _patch_settings(monkeypatch, qbit_service, *, url="http://qb"):
    monkeypatch.setattr(dashboard, "get_settings", lambda: SimpleNamespace(qbittorrent_url=url))
    monkeypatch.setattr(dashboard, "QbittorrentService", lambda settings: qbit_service)


@pytest.mark.asyncio
async def test_completed_endpoint_groups_terminal_request_torrents(monkeypatch):
    managed = _managed(42, info_hash="abc123", title="Managed Release")
    _patch_settings(
        monkeypatch,
        SimpleNamespace(
            get_completed_torrents=AsyncMock(
                return_value=[
                    {"hash": "abc123", "name": "Managed Release", "progress": 1.0, "uploaded": 3}
                ]
            )
        ),
    )

    payload = await dashboard.qbit_completed_torrents_api(
        db=_db(managed, RequestStatus.COMPLETED, (42, "Managed Show", MediaType.TV))
    )

    assert payload["qbit_unavailable"] is False
    group = payload["groups"][0]
    assert group["request_id"] == 42
    assert group["title"] == "Managed Show"
    assert group["totals"]["uploaded"] == 3


@pytest.mark.asyncio
async def test_completed_endpoint_reports_qbit_unavailable(monkeypatch):
    _patch_settings(
        monkeypatch,
        SimpleNamespace(get_completed_torrents=AsyncMock(side_effect=RuntimeError("boom"))),
    )

    payload = await dashboard.qbit_completed_torrents_api(db=MagicMock())

    assert payload == {"groups": [], "qbit_unavailable": True}


@pytest.mark.asyncio
async def test_completed_endpoint_without_qbit_configured(monkeypatch):
    _patch_settings(monkeypatch, SimpleNamespace(), url=None)

    payload = await dashboard.qbit_completed_torrents_api(db=MagicMock())

    assert payload == {"groups": [], "qbit_unavailable": True}


@pytest.mark.asyncio
async def test_downloads_endpoint_reports_qbit_unavailable(monkeypatch):
    _patch_settings(
        monkeypatch,
        SimpleNamespace(get_unfinished_torrents_or_raise=AsyncMock(side_effect=RuntimeError())),
    )

    payload = await dashboard.qbit_downloads_api(db=MagicMock())

    assert payload == {"groups": [], "qbit_unavailable": True}
