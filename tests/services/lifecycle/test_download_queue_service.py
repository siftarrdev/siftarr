from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.siftarr.models.episode import Episode
from app.siftarr.models.request import MediaType, Request, RequestStatus
from app.siftarr.models.season import Season
from app.siftarr.services.lifecycle.download_queue_service import DownloadQueueService


def _torrent(**kwargs):
    defaults = {
        "id": 1,
        "request_id": None,
        "title": "Show.S01E02.1080p",
        "info_hash": "a" * 40,
        "magnet_url": None,
        "status": "approved",
        "move_status": "pending",
        "move_error": "err",
        "moved_path": "/tmp/file",
        "moved_at": object(),
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


@pytest.mark.asyncio
async def test_delete_download_deletes_qbit_with_files_and_discards():
    qb = AsyncMock()
    qb.get_torrent_info.return_value = {"hash": "b" * 40}
    qb.delete_torrent.return_value = True
    torrent = _torrent()

    result = await DownloadQueueService(AsyncMock(), qb).delete_download(torrent)

    assert result.success is True
    qb.delete_torrent.assert_awaited_once_with("b" * 40, delete_files=True)
    assert torrent.status == "discarded"
    assert torrent.moved_path is None


@pytest.mark.asyncio
async def test_delete_download_missing_qbit_torrent_is_success():
    qb = AsyncMock()
    qb.get_torrent_info.return_value = None
    qb.get_torrent_info_by_name.return_value = None
    torrent = _torrent()

    result = await DownloadQueueService(AsyncMock(), qb).delete_download(torrent)

    assert result.success is True
    qb.delete_torrent.assert_not_awaited()
    assert torrent.status == "discarded"


@pytest.mark.asyncio
async def test_delete_download_qbit_failure_keeps_state_unchanged():
    qb = AsyncMock()
    qb.get_torrent_info.return_value = {"hash": "b" * 40}
    qb.delete_torrent.return_value = False
    torrent = _torrent()

    result = await DownloadQueueService(AsyncMock(), qb).delete_download(torrent)

    assert result.success is False
    assert torrent.status == "approved"
    assert torrent.moved_path == "/tmp/file"


@pytest.mark.asyncio
async def test_delete_download_lookup_failure_keeps_torrent_and_request_unchanged(
    monkeypatch: pytest.MonkeyPatch,
):
    qb = AsyncMock()
    qb.get_torrent_info.side_effect = RuntimeError("qbit unavailable")
    qb.get_torrent_info_by_name.return_value = None
    request = Request(
        external_id="req-99",
        media_type=MediaType.MOVIE,
        title="Movie",
        status=RequestStatus.DOWNLOADING,
    )
    torrent = _torrent(request_id=99)
    service = DownloadQueueService(AsyncMock(), qb)
    monkeypatch.setattr(service, "_load_request", AsyncMock(return_value=request))

    result = await service.delete_download(torrent)

    assert result.success is False
    assert "look up" in (result.message or "")
    qb.delete_torrent.assert_not_awaited()
    assert torrent.status == "approved"
    assert torrent.moved_path == "/tmp/file"
    assert request.status == RequestStatus.DOWNLOADING


def test_reset_tv_covered_episodes_only_resets_matching_non_completed():
    request = Request(
        external_id="req-tv",
        media_type=MediaType.TV,
        title="Show",
        status=RequestStatus.DOWNLOADING,
    )
    season = Season(season_number=1, status=RequestStatus.DOWNLOADING)
    ep1 = Episode(episode_number=1, status=RequestStatus.COMPLETED)
    ep2 = Episode(episode_number=2, status=RequestStatus.DOWNLOADING)
    ep3 = Episode(episode_number=3, status=RequestStatus.DOWNLOADING)
    season.episodes = [ep1, ep2, ep3]
    request.seasons = [season]

    DownloadQueueService(AsyncMock(), AsyncMock())._reset_tv_covered_episodes(
        request, "Show.S01E02.1080p"
    )

    assert [ep1.status, ep2.status, ep3.status] == [
        RequestStatus.COMPLETED,
        RequestStatus.PENDING,
        RequestStatus.DOWNLOADING,
    ]
