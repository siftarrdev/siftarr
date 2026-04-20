from unittest.mock import AsyncMock, MagicMock

import pytest

from app.siftarr.models.staged_torrent import StagedTorrent
from app.siftarr.services import release_selection_service


@pytest.mark.asyncio
async def test_use_releases_keeps_existing_staged_release(
    mock_db, request_record, selected_release
):
    settings = MagicMock(staging_mode_enabled=True)
    queue_service = AsyncMock()
    staging_service = AsyncMock()

    existing_stage = MagicMock()
    existing_stage.id = 44
    existing_stage.title = selected_release.title
    existing_result = MagicMock()
    existing_result.scalars.return_value.all.return_value = [existing_stage]
    mock_db.execute.return_value = existing_result

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(
            release_selection_service,
            "get_effective_settings",
            AsyncMock(return_value=settings),
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
            selection_source="rule",
        )

    assert result["status"] == "staged"
    assert result["action"] == "auto_staged"
    assert result["message"] == "Auto-staged 1 release(s) for approval."
    assert result["staged_ids"] == [existing_stage.id]
    staging_service.save_release.assert_not_awaited()
    queue_service.remove_from_queue.assert_awaited_once_with(request_record.id)


@pytest.mark.asyncio
async def test_use_releases_replaces_existing_active_stage_for_manual_selection(
    mock_db, request_record, selected_release
):
    settings = MagicMock(staging_mode_enabled=True)
    queue_service = AsyncMock()
    staging_service = AsyncMock()

    existing_stage = StagedTorrent(
        id=44,
        request_id=request_record.id,
        torrent_path="/tmp/existing.torrent",
        json_path="/tmp/existing.json",
        original_filename="existing",
        title="Auto Pick",
        size=1,
        indexer="Indexer A",
        score=100,
        status="staged",
        selection_source="rule",
    )
    replacement_stage = StagedTorrent(
        id=55,
        request_id=request_record.id,
        torrent_path="/tmp/replacement.torrent",
        json_path="/tmp/replacement.json",
        original_filename="replacement",
        title=selected_release.title,
        size=selected_release.size,
        indexer=selected_release.indexer,
        score=selected_release.score,
        status="staged",
        selection_source="manual",
    )
    staging_service.save_release.return_value = replacement_stage

    active_result = MagicMock()
    active_result.scalars.return_value.all.return_value = [existing_stage]
    mock_db.execute.return_value = active_result

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(
            release_selection_service,
            "get_effective_settings",
            AsyncMock(return_value=settings),
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
    assert result["action"] == "replaced_active_selection"
    assert result["message"] == "Replaced the active staged selection with 1 release(s)."
    assert result["staged_ids"] == [replacement_stage.id]
    assert existing_stage.status == "replaced"
    assert existing_stage.replaced_by_id == replacement_stage.id
    assert existing_stage.replaced_at is not None
    assert (
        existing_stage.replacement_reason
        == "Manually replaced staged selection from request details"
    )
    queue_service.remove_from_queue.assert_awaited_once_with(request_record.id)


@pytest.mark.asyncio
async def test_use_releases_reuses_existing_manual_pick_and_retires_auto_pick(
    mock_db, request_record, selected_release
):
    settings = MagicMock(staging_mode_enabled=True)
    queue_service = AsyncMock()
    staging_service = AsyncMock()

    auto_stage = StagedTorrent(
        id=44,
        request_id=request_record.id,
        torrent_path="/tmp/auto.torrent",
        json_path="/tmp/auto.json",
        original_filename="auto",
        title="Auto Pick",
        size=1,
        indexer="Indexer A",
        score=100,
        status="staged",
        selection_source="rule",
    )
    manual_stage = StagedTorrent(
        id=55,
        request_id=request_record.id,
        torrent_path="/tmp/manual.torrent",
        json_path="/tmp/manual.json",
        original_filename="manual",
        title=selected_release.title,
        size=selected_release.size,
        indexer=selected_release.indexer,
        score=selected_release.score,
        status="staged",
        selection_source="manual",
    )

    active_result = MagicMock()
    active_result.scalars.return_value.all.return_value = [auto_stage, manual_stage]
    mock_db.execute.return_value = active_result

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(
            release_selection_service,
            "get_effective_settings",
            AsyncMock(return_value=settings),
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
    assert result["action"] == "replaced_active_selection"
    assert result["message"] == "Replaced the active staged selection with 1 release(s)."
    assert result["staged_ids"] == [manual_stage.id]
    assert auto_stage.status == "replaced"
    assert auto_stage.replaced_by_id == manual_stage.id
    assert manual_stage.status == "staged"
    staging_service.save_release.assert_not_awaited()


@pytest.mark.asyncio
async def test_use_releases_tv_single_episode_reuses_same_episode_stage_without_replacing_sibling(
    mock_db,
):
    settings = MagicMock(staging_mode_enabled=True)
    queue_service = AsyncMock()
    staging_service = AsyncMock()

    request_record = MagicMock()
    request_record.id = 10
    request_record.media_type = release_selection_service.MediaType.TV
    request_record.status = release_selection_service.RequestStatus.PENDING

    selected_release = MagicMock()
    selected_release.id = 201
    selected_release.title = "Show.S01E01.1080p.WEB-DL"
    selected_release.score = 50
    selected_release.size = 1_000
    selected_release.seeders = 10
    selected_release.leechers = 1
    selected_release.indexer = "Indexer A"
    selected_release.magnet_url = "magnet:?xt=urn:btih:e1"
    selected_release.download_url = "https://example.com/e1.torrent"
    selected_release.info_hash = None
    selected_release.publish_date = None
    selected_release.resolution = None
    selected_release.codec = None
    selected_release.release_group = None

    same_episode_stage = StagedTorrent(
        id=71,
        request_id=request_record.id,
        torrent_path="/tmp/e1.torrent",
        json_path="/tmp/e1.json",
        original_filename="e1",
        title=selected_release.title,
        size=selected_release.size,
        indexer=selected_release.indexer,
        score=selected_release.score,
        status="staged",
        selection_source="manual",
    )
    sibling_stage = StagedTorrent(
        id=72,
        request_id=request_record.id,
        torrent_path="/tmp/e2.torrent",
        json_path="/tmp/e2.json",
        original_filename="e2",
        title="Show.S01E02.1080p.WEB-DL",
        size=1_100,
        indexer="Indexer A",
        score=55,
        status="staged",
        selection_source="manual",
    )

    active_result = MagicMock()
    active_result.scalars.return_value.all.return_value = [same_episode_stage, sibling_stage]
    mock_db.execute.return_value = active_result

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(
            release_selection_service,
            "get_effective_settings",
            AsyncMock(return_value=settings),
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
    assert result["staged_ids"] == [same_episode_stage.id]
    assert same_episode_stage.status == "staged"
    assert sibling_stage.status == "staged"
    assert sibling_stage.replaced_by_id is None
    staging_service.save_release.assert_not_awaited()
