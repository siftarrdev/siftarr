"""Tests for the dashboard page route and the /api/downloads endpoint."""

import json
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.siftarr.models import ActivityLog, StagedTorrent
from app.siftarr.models.activity_log import EventType
from app.siftarr.models.request import MediaType, RequestStatus
from app.siftarr.routers import dashboard
from app.siftarr.routers.dashboard import _is_actionable_workflow_torrent
from tests.factories import make_episode, make_request, make_season, make_staged_torrent

P, S, C, U = (
    RequestStatus.PENDING,
    RequestStatus.SEARCHING,
    RequestStatus.COMPLETED,
    RequestStatus.UNRELEASED,
)


@pytest.fixture(autouse=True)
def _dashboard_env(monkeypatch):
    """Settings, a failing qBittorrent client, and an Overseerr stub that reports a future air date."""
    monkeypatch.setattr(
        dashboard,
        "get_settings",
        lambda: MagicMock(
            overseerr_url="http://overseerr.test",
            staging_mode_enabled=True,
            qbittorrent_url="http://qb.test",
        ),
    )
    overseerr = AsyncMock()
    overseerr.get_media_details.return_value = {"nextEpisodeToAir": {"airDate": "2026-05-01"}}
    monkeypatch.setattr(dashboard, "OverseerrService", lambda settings: overseerr)
    # qBittorrent is down: the managed queue is shown and labelled unavailable.
    qbit = AsyncMock()
    qbit.get_unfinished_torrents_or_raise.side_effect = RuntimeError("connection refused")
    monkeypatch.setattr(dashboard, "QbittorrentService", lambda settings: qbit)


async def _render(db):
    await db.commit()
    return await dashboard.dashboard(MagicMock(), db=db)


def _tabs(context, request) -> set[str]:
    """Names of the context lists that hold ``request``."""
    names = ("active_requests", "pending_requests", "unreleased_requests", "completed_requests")
    return {name for name in names if any(r.id == request.id for r in context[name])}


@pytest.mark.parametrize(
    ("media_type", "status", "episode_statuses", "expected_tabs"),
    [
        # Persisted UNRELEASED never leaks into active or finished.
        (MediaType.TV, U, [], {"unreleased_requests"}),
        # Pending episodes keep a partially available show on the pending tab.
        (MediaType.TV, P, [C, P], {"active_requests", "pending_requests"}),
        # Only completed and unreleased episodes left: unreleased tab only.
        (MediaType.TV, P, [C, U], {"unreleased_requests"}),
        # A finished show that gains an unreleased episode leaves the finished tab.
        (MediaType.TV, C, [C, U], {"unreleased_requests"}),
        (MediaType.TV, S, [], {"active_requests", "pending_requests"}),
        (MediaType.MOVIE, C, [], {"completed_requests"}),
    ],
)
async def test_request_lands_on_one_tab(db, media_type, status, episode_statuses, expected_tabs):
    req = await make_request(db, media_type=media_type, status=status, title="Subject")
    if episode_statuses:
        season = await make_season(db, req, 1)
        for number, episode_status in enumerate(episode_statuses, start=1):
            await make_episode(db, season, number, status=episode_status)

    response = await _render(db)

    context = response.context
    tabs = _tabs(context, req)
    if "pending_requests" in expected_tabs:
        # The finished query returns every TV request; the template ignores non-completed ones.
        tabs.discard("completed_requests")
    assert tabs == expected_tabs
    assert "Subject" in response.body.decode()
    stats = context["stats"]
    assert stats["unreleased"] == int("unreleased_requests" in expected_tabs)
    assert stats["pending"] == int("pending_requests" in expected_tabs)
    if "pending_requests" not in expected_tabs:
        assert stats["completed"] == int("completed_requests" in expected_tabs)
    if "unreleased_requests" in expected_tabs:
        assert context["unreleased_release_dates"][req.id] == "2026-05-01"


async def test_pending_tab_shows_tv_completion_progress(db):
    show = await make_request(db, media_type=MediaType.TV, status=S, title="Severance")
    await make_season(db, show, 1, episodes=1, status=C)
    season_two = await make_season(db, show, 2, episodes=1, status=C)
    await make_episode(db, season_two, 2, status=P)

    rendered = (await _render(db)).body.decode()

    assert "1/2 seasons" in rendered
    assert "S02: 1/2 episodes" in rendered


async def test_downloading_and_staged_tabs_split_by_torrent_and_request_status(db):
    """Approved rows stay for any non-terminal request, including a TV request that
    derives back to PENDING mid-download. Staged rows stay even for PENDING/SEARCHING
    requests and for torrents with no request."""
    downloading = await make_request(
        db, status=RequestStatus.DOWNLOADING, title="Downloading Movie", external_id="1"
    )
    finished = await make_request(db, status=C, title="Finished Movie", external_id="2")
    tv_pending = await make_request(
        db, media_type=MediaType.TV, status=P, title="Mixed Show", external_id="3"
    )
    tv_searching = await make_request(
        db, media_type=MediaType.TV, status=S, title="Searching Show", external_id="4"
    )
    staged_movie = await make_request(
        db, status=RequestStatus.STAGED, title="Staged Movie", external_id="5"
    )
    active = await make_staged_torrent(
        db, downloading, status="approved", title="Downloading Movie 2025"
    )
    hidden = await make_staged_torrent(db, finished, status="approved", title="Hidden Torrent")
    tv_approved = await make_staged_torrent(
        db, tv_pending, status="approved", title="Mixed Show S01E01 1080p"
    )
    staged = [
        await make_staged_torrent(db, tv_searching, title="Searching Show S02 1080p"),
        await make_staged_torrent(db, staged_movie, title="Staged Movie 2025"),
        await make_staged_torrent(db, None, title="Manual Torrent"),
    ]

    response = await _render(db)

    context = response.context
    assert {t.id for t in context["staged_torrents"]} == {t.id for t in staged}
    assert {t.id for t in context["downloading_torrents"]} == {active.id, tv_approved.id}
    assert hidden not in context["downloading_torrents"]
    assert context["stats"]["staged"] == 3
    assert context["downloading_request_statuses"] == {
        downloading.id: RequestStatus.DOWNLOADING.value,
        tv_pending.id: P.value,
    }
    body = response.body.decode()
    assert "Manual Torrent" in body
    assert "Mixed Show S01E01 1080p" in body
    assert "Hidden Torrent" not in body


async def test_tv_staged_rows_stay_approvable_after_one_episode_is_approved(db):
    show = await make_request(
        db, media_type=MediaType.TV, status=RequestStatus.DOWNLOADING, title="Example Show"
    )
    await make_staged_torrent(db, show, status="approved", title="Example Show S01E01")
    staged = [await make_staged_torrent(db, show, title=f"Example Show S01E0{n}") for n in (2, 3)]

    response = await _render(db)

    assert response.context["replace_staged_torrent_ids"] == set()
    body = response.body.decode()
    for torrent in staged:
        assert f"postStagedAction('/staged/{torrent.id}/approve', '/?tab=staged')" in body
        assert f"openReplaceModal({torrent.id}" not in body


async def test_downloading_tab_lists_every_bulk_approved_movie_with_move_status(db):
    torrents = []
    for n, title in enumerate(("First Bulk Movie", "Second Bulk Movie"), start=1):
        req = await make_request(
            db, status=RequestStatus.DOWNLOADING, title=title, external_id=str(n)
        )
        torrents.append(
            await make_staged_torrent(
                db,
                req,
                status="approved",
                title=f"{title} 2025",
                move_status="moved" if n == 1 else None,
                moved_path="/media/movies" if n == 1 else None,
            )
        )

    response = await _render(db)

    assert {t.id for t in response.context["downloading_torrents"]} == {t.id for t in torrents}
    assert response.context["stats"]["downloading"] == 2
    body = response.body.decode()
    assert 'data-movestatus="moved"' in body
    assert "data-move-status>moved</span>" in body
    assert "/media/movies" in body


async def test_only_the_completed_episode_torrent_is_marked_waiting_for_plex(db):
    show = await make_request(
        db, media_type=MediaType.TV, status=RequestStatus.DOWNLOADING, title="Test Show"
    )
    done = await make_staged_torrent(db, show, status="approved", title="Test Show S01E01")
    other = await make_staged_torrent(db, show, status="approved", title="Test Show S01E02")
    db.add(
        ActivityLog(
            request_id=show.id,
            event_type=EventType.DOWNLOAD_COMPLETED.value,
            details=json.dumps({"done_torrents": [{"torrent_id": done.id}]}),
        )
    )

    response = await _render(db)

    assert response.context["downloading_waiting_plex_torrent_ids"] == {done.id}
    rendered = response.body.decode()
    assert f'data-torrent-id="{other.id}"' in rendered
    assert "qBittorrent finished; waiting for Plex" in rendered
    # Each torrent renders in a mobile and a desktop layout.
    assert rendered.count('data-qbit-finished-waiting-plex="true"') == 2
    assert rendered.count('data-qbit-finished-waiting-plex="false"') == 2


@pytest.mark.parametrize(
    ("request_title", "year", "release_title", "warns"),
    [
        ("The Cheetah Girls", 2003, "The.Cheetah.Girls.2.2005.1080p.WEB-DL", True),
        ("Blade Runner 2049", 2017, "Blade.Runner.2049.2017.1080p.WEB-DL", False),
    ],
)
async def test_staged_movie_identity_mismatch_warning(
    db, request_title, year, release_title, warns
):
    req = await make_request(db, status=RequestStatus.STAGED, title=request_title, year=year)
    torrent = await make_staged_torrent(db, req, title=release_title)

    response = await _render(db)

    warnings = response.context["movie_identity_mismatch_warnings"]
    assert (torrent.id in warnings) is warns
    assert ("Possible wrong movie" in response.body.decode()) is warns


@pytest.mark.parametrize(
    ("status", "torrent_status", "expected"),
    [
        (P, "staged", True),
        (RequestStatus.DOWNLOADING, "approved", True),
        # A TV request can derive back to PENDING while an approved torrent downloads.
        (P, "approved", True),
        (C, "approved", False),
    ],
)
def test_actionable_workflow_torrent(status, torrent_status, expected):
    torrent = cast(StagedTorrent, SimpleNamespace(request_id=42, status=torrent_status))

    assert _is_actionable_workflow_torrent(torrent, {42: status}) is expected


@pytest.mark.parametrize(
    ("status", "managed"),
    [(P, True), (RequestStatus.DOWNLOADING, True), (C, False)],
)
async def test_downloads_api_matches_managed_torrents_unless_request_is_terminal(
    db, monkeypatch, status, managed
):
    """A single-episode approval derives a TV request back to PENDING, but the torrent is
    still downloading and must stay managed. Terminal requests drop to unmanaged."""
    show = await make_request(db, media_type=MediaType.TV, status=status, title="Managed Show")
    torrent = await make_staged_torrent(
        db, show, status="approved", info_hash="abc123", title="Managed Release"
    )
    qbit = SimpleNamespace(
        get_unfinished_torrents_or_raise=AsyncMock(
            return_value=[
                {"hash": "abc123", "name": "Managed Release", "progress": 0.5},
                {"hash": "manual", "name": "Manual", "progress": 0.25},
            ]
        )
    )
    monkeypatch.setattr(
        dashboard, "get_settings", lambda: SimpleNamespace(qbittorrent_url="http://qb")
    )
    monkeypatch.setattr(dashboard, "QbittorrentService", lambda settings: qbit)
    await db.commit()

    payload = await dashboard.qbit_downloads_api(db=db)

    assert payload["qbit_unavailable"] is False
    groups = payload["groups"]
    if managed:
        assert [group["title"] for group in groups] == ["Managed Show", "Unmanaged"]
        assert groups[0]["request_id"] == show.id
        assert groups[0]["media_type"] == "tv"
        assert groups[0]["torrents"][0]["managed"] == {
            "id": torrent.id,
            "request_id": show.id,
            "move_status": "pending",
            "moved_path": None,
        }
    else:
        assert [group["unmanaged"] for group in groups] == [True]
        assert {t["managed"] for t in groups[0]["torrents"]} == {None}
    assert groups[-1]["unmanaged"] is True
    assert groups[-1]["torrents"][0]["managed"] is None
