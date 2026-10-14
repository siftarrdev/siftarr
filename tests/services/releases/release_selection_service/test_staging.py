from datetime import date
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import select

from app.siftarr.models import Episode, Season
from app.siftarr.models.release import Release
from app.siftarr.models.request import MediaType, Request, RequestStatus
from app.siftarr.models.staged_torrent import StagedTorrent
from app.siftarr.services.releases import staging_service as svc
from app.siftarr.services.releases.staging_service import StagingService
from tests.factories import (
    make_release,
    make_request,
    make_season,
    make_staged_torrent,
)

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _staging_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(svc, "STAGING_DIR", tmp_path)


@pytest.fixture
def qbittorrent(monkeypatch):
    client = MagicMock()
    client.add_torrent = AsyncMock(return_value="a" * 40)
    monkeypatch.setattr(svc, "QbittorrentService", MagicMock(return_value=client))
    return client


def _staging_mode(monkeypatch, enabled: bool) -> None:
    monkeypatch.setattr(svc, "get_settings", lambda: MagicMock(staging_mode_enabled=enabled))


async def _staged_titles(db) -> list[str]:
    rows = (await db.execute(select(StagedTorrent.title).order_by(StagedTorrent.title))).scalars()
    return list(rows)


async def test_manual_selection_stages_release_and_clears_retry_state(db, monkeypatch):
    _staging_mode(monkeypatch, True)
    request = await make_request(db, retry_count=3, next_retry_at=date(2030, 1, 1))
    release = await make_release(db, request, magnet_url="magnet:?xt=urn:btih:1")

    result = await StagingService(db).use_releases(request, [release], selection_source="manual")

    staged = (await db.execute(select(StagedTorrent))).scalar_one()
    assert result["action"] == "manual_staged"
    assert result["staged_ids"] == [staged.id]
    assert staged.selection_source == "manual"
    assert request.status == RequestStatus.STAGED
    assert request.next_retry_at is None


async def test_staging_disabled_sends_to_qbittorrent_and_tracks_approved_row(
    db, monkeypatch, qbittorrent
):
    _staging_mode(monkeypatch, False)
    request = await make_request(db)
    release = await make_release(db, request, magnet_url="magnet:?xt=urn:btih:abc")

    result = await StagingService(db).use_releases(request, [release], selection_source="manual")

    assert result["status"] == "downloading"
    assert result["torrent_hashes"] == ["a" * 40]
    qbittorrent.add_torrent.assert_awaited_once()
    tracked = (await db.execute(select(StagedTorrent))).scalar_one()
    assert (tracked.status, tracked.info_hash) == ("approved", "a" * 40)
    assert request.status == RequestStatus.DOWNLOADING


async def test_force_download_bypasses_staging_and_retires_active_stage(
    db, monkeypatch, qbittorrent
):
    _staging_mode(monkeypatch, True)
    request = await make_request(db)
    await make_staged_torrent(db, request, title="Example Movie 2025 720p")
    release = await make_release(db, request, magnet_url="magnet:?xt=urn:btih:abc")

    result = await StagingService(db).use_releases(
        request, [release], selection_source="manual", force_download=True
    )

    assert result["status"] == "downloading"
    assert "Example Movie 2025 720p" not in await _staged_titles(db)


def _tv_release(request: Request, number: int, title: str) -> Release:
    return Release(
        request_id=request.id,
        title=title,
        size=1_000,
        seeders=10,
        leechers=1,
        download_url=f"https://example.com/{number}.torrent",
        magnet_url=f"magnet:?xt=urn:btih:{number}",
        indexer="Indexer A",
        score=50,
        passed_rules=True,
    )


E01 = "Show.S01E01.1080p.WEB-DL"
E02 = "Show.S01E02.1080p.WEB-DL"
E01_REPACK = "Show.S01E01.REPACK.1080p.WEB-DL"
S01 = "Show.S01.1080p.WEB-DL"
S01_REPACK = "Show.S01.REPACK.1080p.WEB-DL"
S02 = "Show.S02.1080p.WEB-DL"
COMPLETE = "Show.Complete.Series.1080p.WEB-DL"


@pytest.mark.parametrize(
    ("selections", "expected_titles", "last_action"),
    [
        ([E01, E02, E01_REPACK], [E01_REPACK, E02], "replaced_active_selection"),
        ([S01, S02], [S01, S02], "manual_staged"),
        ([S01, S02, S01_REPACK], [S01_REPACK, S02], "replaced_active_selection"),
        ([S02, E01], [E01, S02], "manual_staged"),
        ([S01, COMPLETE], [COMPLETE], "replaced_active_selection"),
    ],
    ids=[
        "same-episode-replaced-sibling-kept",
        "different-season-packs-coexist",
        "same-season-pack-replaced-other-season-kept",
        "episode-and-other-season-pack-coexist",
        "complete-series-replaces-all",
    ],
)
async def test_tv_manual_selection_replaces_only_overlapping_stages(
    db, monkeypatch, selections, expected_titles, last_action
):
    _staging_mode(monkeypatch, True)
    request = await make_request(db, media_type=MediaType.TV, title="Show")
    for number in (1, 2):
        await make_season(db, request, number, episodes=2)
    service = StagingService(db)

    result = {}
    for number, title in enumerate(selections):
        release = _tv_release(request, number, title)
        db.add(release)
        await db.flush()
        result = await service.use_releases(request, [release], selection_source="manual")

    assert result["action"] == last_action
    assert await _staged_titles(db) == sorted(expected_titles)


async def test_tv_selection_marks_only_covered_episodes_staged(db, monkeypatch):
    _staging_mode(monkeypatch, True)
    request = await make_request(db, media_type=MediaType.TV, title="Show")
    await make_season(db, request, 1, episodes=2)
    release = _tv_release(request, 1, E02)
    db.add(release)
    await db.flush()

    await StagingService(db).use_releases(request, [release], selection_source="manual")

    statuses = {
        ep.episode_number: ep.status for ep in (await db.execute(select(Episode))).scalars()
    }
    assert statuses == {1: RequestStatus.PENDING, 2: RequestStatus.STAGED}
    assert (await db.execute(select(Season))).scalar_one().status != RequestStatus.PENDING


def _scope_request() -> Request:
    return Request(id=9, media_type=MediaType.TV, status=RequestStatus.PENDING)


def _scope_release(title: str) -> Release:
    return Release(id=1, title=title)


def _staged(sid: int, title: str) -> StagedTorrent:
    return StagedTorrent(id=sid, request_id=9, title=title, status="staged")


@pytest.mark.parametrize(
    ("incoming", "active", "expected_conflicts"),
    [
        (E02, [E01, S02], []),
        (S02, [E01, "Show.S03.1080p.WEB-DL"], []),
        (
            E01,
            [S01, "Show.S01-S02.1080p", "Show.S03-S04.1080p", COMPLETE],
            [S01, "Show.S01-S02.1080p", COMPLETE],
        ),
        (
            "Show.S02-S03.1080p",
            [S01, "Show.S01-S02.1080p", "Show.S03-S04.1080p", COMPLETE],
            ["Show.S01-S02.1080p", "Show.S03-S04.1080p", COMPLETE],
        ),
    ],
    ids=[
        "episode-vs-other-episode-and-season",
        "season-vs-episode-and-other-season",
        "episode-covered-by-packs",
        "multi-season-overlap",
    ],
)
def test_tv_conflict_scope(incoming, active, expected_conflicts):
    staged = [_staged(i, title) for i, title in enumerate(active, start=1)]

    conflicts = svc._filter_active_staged_torrents_for_release(
        _scope_request(), _scope_release(incoming), staged
    )

    assert [row.title for row in conflicts] == expected_conflicts
