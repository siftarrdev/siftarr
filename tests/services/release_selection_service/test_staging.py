from unittest.mock import AsyncMock, MagicMock

import pytest

from app.siftarr.models.staged_torrent import StagedTorrent
from app.siftarr.services import release_selection_service


@pytest.mark.asyncio
async def test_use_releases_marks_manual_selection_source(
    mock_db, request_record, selected_release
):
    settings = MagicMock(staging_mode_enabled=True)
    queue_service = AsyncMock()
    staging_service = AsyncMock()
    staged_record = MagicMock(id=33)
    staging_service.save_release.return_value = staged_record

    existing_result = MagicMock()
    existing_result.scalar_one_or_none.return_value = None
    mock_db.execute.return_value = existing_result

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(
            release_selection_service,
            "get_settings",
            MagicMock(return_value=settings),
        )
        monkeypatch.setattr(
            release_selection_service,
            "PendingQueueService",
            MagicMock(return_value=queue_service),
        )
        monkeypatch.setattr(
            release_selection_service,
            "StagingService",
            MagicMock(return_value=staging_service),
        )

        result = await release_selection_service.use_releases(
            mock_db,
            request_record,
            [selected_release],
            selection_source="manual",
        )

    assert result["status"] == "staged"
    assert result["action"] == "manual_staged"
    assert result["message"] == "Manually staged 1 release(s) for approval."
    staging_service.save_release.assert_awaited_once()
    assert staging_service.save_release.await_args.kwargs["selection_source"] == "manual"
    queue_service.remove_from_queue.assert_awaited_once_with(request_record.id)


@pytest.mark.asyncio
async def test_use_releases_sends_direct_when_staging_disabled(
    mock_db, request_record, selected_release
):
    settings = MagicMock(staging_mode_enabled=False)
    queue_service = AsyncMock()
    qbittorrent_service = AsyncMock()
    qbittorrent_service.add_torrent.return_value = "abc123"
    mock_db.commit = AsyncMock()

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(
            release_selection_service,
            "get_settings",
            MagicMock(return_value=settings),
        )
        monkeypatch.setattr(
            release_selection_service,
            "PendingQueueService",
            MagicMock(return_value=queue_service),
        )
        monkeypatch.setattr(
            release_selection_service,
            "QbittorrentService",
            MagicMock(return_value=qbittorrent_service),
        )

        result = await release_selection_service.use_releases(
            mock_db,
            request_record,
            [selected_release],
            selection_source="manual",
        )

    assert result["status"] == "downloading"
    assert result["torrent_hashes"] == ["abc123"]
    qbittorrent_service.add_torrent.assert_awaited_once()
    queue_service.remove_from_queue.assert_awaited_once_with(request_record.id)


@pytest.mark.asyncio
async def test_use_releases_tv_single_episode_selection_only_replaces_same_episode_stage(mock_db):
    settings = MagicMock(staging_mode_enabled=True)
    queue_service = AsyncMock()
    staging_service = AsyncMock()

    request_record = MagicMock()
    request_record.id = 9
    request_record.media_type = release_selection_service.MediaType.TV
    request_record.status = release_selection_service.RequestStatus.PENDING

    episode_one_release = MagicMock()
    episode_one_release.id = 101
    episode_one_release.title = "Show.S01E01.1080p.WEB-DL"
    episode_one_release.score = 50
    episode_one_release.size = 1_000
    episode_one_release.seeders = 10
    episode_one_release.leechers = 1
    episode_one_release.indexer = "Indexer A"
    episode_one_release.magnet_url = "magnet:?xt=urn:btih:e1"
    episode_one_release.download_url = "https://example.com/e1.torrent"
    episode_one_release.info_hash = None
    episode_one_release.publish_date = None
    episode_one_release.resolution = None
    episode_one_release.codec = None
    episode_one_release.release_group = None

    episode_two_release = MagicMock()
    episode_two_release.id = 102
    episode_two_release.title = "Show.S01E02.1080p.WEB-DL"
    episode_two_release.score = 55
    episode_two_release.size = 1_100
    episode_two_release.seeders = 12
    episode_two_release.leechers = 1
    episode_two_release.indexer = "Indexer A"
    episode_two_release.magnet_url = "magnet:?xt=urn:btih:e2"
    episode_two_release.download_url = "https://example.com/e2.torrent"
    episode_two_release.info_hash = None
    episode_two_release.publish_date = None
    episode_two_release.resolution = None
    episode_two_release.codec = None
    episode_two_release.release_group = None

    reselection_release = MagicMock()
    reselection_release.id = 103
    reselection_release.title = "Show.S01E01.REPACK.1080p.WEB-DL"
    reselection_release.score = 60
    reselection_release.size = 1_050
    reselection_release.seeders = 15
    reselection_release.leechers = 1
    reselection_release.indexer = "Indexer B"
    reselection_release.magnet_url = "magnet:?xt=urn:btih:e1repack"
    reselection_release.download_url = "https://example.com/e1-repack.torrent"
    reselection_release.info_hash = None
    reselection_release.publish_date = None
    reselection_release.resolution = None
    reselection_release.codec = None
    reselection_release.release_group = None

    stage_episode_one = StagedTorrent(
        id=61,
        request_id=request_record.id,
        torrent_path="/tmp/e1.torrent",
        json_path="/tmp/e1.json",
        original_filename="e1",
        title=episode_one_release.title,
        size=episode_one_release.size,
        indexer=episode_one_release.indexer,
        score=episode_one_release.score,
        status="staged",
        selection_source="manual",
    )
    stage_episode_two = StagedTorrent(
        id=62,
        request_id=request_record.id,
        torrent_path="/tmp/e2.torrent",
        json_path="/tmp/e2.json",
        original_filename="e2",
        title=episode_two_release.title,
        size=episode_two_release.size,
        indexer=episode_two_release.indexer,
        score=episode_two_release.score,
        status="staged",
        selection_source="manual",
    )
    stage_episode_one_replacement = StagedTorrent(
        id=63,
        request_id=request_record.id,
        torrent_path="/tmp/e1-repack.torrent",
        json_path="/tmp/e1-repack.json",
        original_filename="e1-repack",
        title=reselection_release.title,
        size=reselection_release.size,
        indexer=reselection_release.indexer,
        score=reselection_release.score,
        status="staged",
        selection_source="manual",
    )

    active_result_initial = MagicMock()
    active_result_initial.scalars.return_value.all.return_value = []
    active_result_after_episode_one = MagicMock()
    active_result_after_episode_one.scalars.return_value.all.return_value = [stage_episode_one]
    active_result_before_reselection = MagicMock()
    active_result_before_reselection.scalars.return_value.all.return_value = [
        stage_episode_one,
        stage_episode_two,
    ]
    mock_db.execute.side_effect = [
        active_result_initial,
        active_result_after_episode_one,
        active_result_before_reselection,
    ]
    staging_service.save_release.side_effect = [
        stage_episode_one,
        stage_episode_two,
        stage_episode_one_replacement,
    ]

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(
            release_selection_service,
            "get_settings",
            MagicMock(return_value=settings),
        )
        monkeypatch.setattr(
            release_selection_service,
            "PendingQueueService",
            MagicMock(return_value=queue_service),
        )
        monkeypatch.setattr(
            release_selection_service,
            "StagingService",
            MagicMock(return_value=staging_service),
        )

        first_result = await release_selection_service.use_releases(
            mock_db,
            request_record,
            [episode_one_release],
            selection_source="manual",
        )
        second_result = await release_selection_service.use_releases(
            mock_db,
            request_record,
            [episode_two_release],
            selection_source="manual",
        )
        reselection_result = await release_selection_service.use_releases(
            mock_db,
            request_record,
            [reselection_release],
            selection_source="manual",
        )

    assert first_result["action"] == "manual_staged"
    assert second_result["action"] == "manual_staged"
    assert reselection_result["status"] == "staged"
    assert reselection_result["action"] == "replaced_active_selection"
    assert reselection_result["staged_ids"] == [stage_episode_one_replacement.id]
    mock_db.delete.assert_awaited_once_with(stage_episode_one)
    assert stage_episode_two.status == "staged"
    assert staging_service.save_release.await_count == 3
    queue_service.remove_from_queue.assert_awaited()
