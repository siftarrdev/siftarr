from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.siftarr.models.request import MediaType, RequestStatus
from app.siftarr.models.staged_torrent import StagedTorrent
from app.siftarr.services import staging_service as svc
from app.siftarr.services.staging_service import StagingService


@patch.object(svc, "PendingQueueService")
@patch.object(svc, "get_settings")
@pytest.mark.asyncio
async def test_use_releases_marks_manual_selection_source(
    mock_get_settings, mock_pq_cls, mock_db, request_record, selected_release
):
    settings = MagicMock(staging_mode_enabled=True)
    mock_get_settings.return_value = settings
    queue_service = AsyncMock()
    mock_pq_cls.return_value = queue_service

    mock_db.execute.return_value = MagicMock(
        scalar_one_or_none=MagicMock(return_value=None),
        scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[]))),
    )

    # Mock save_release on the real StagingService
    staged_record = MagicMock(id=33)
    with patch.object(
        StagingService, "save_release", new_callable=AsyncMock, return_value=staged_record
    ) as mock_save:
        staging = StagingService(mock_db)
        result = await staging.use_releases(
            request_record,
            [selected_release],
            selection_source="manual",
        )

    assert result["status"] == "staged"
    assert result["action"] == "manual_staged"
    assert result["message"] == "Manually staged 1 release(s) for approval."
    mock_save.assert_awaited_once()
    kwargs = mock_save.await_args.kwargs if mock_save.await_args else {}
    assert kwargs.get("selection_source") == "manual"
    queue_service.remove_from_queue.assert_awaited_once_with(request_record.id)


@patch.object(svc, "QbittorrentService")
@patch.object(svc, "PendingQueueService")
@patch.object(svc, "get_settings")
@pytest.mark.asyncio
async def test_use_releases_sends_direct_when_staging_disabled(
    mock_get_settings, mock_pq_cls, mock_qb_cls, mock_db, request_record, selected_release
):
    settings = MagicMock(staging_mode_enabled=False)
    mock_get_settings.return_value = settings
    queue_service = AsyncMock()
    mock_pq_cls.return_value = queue_service
    qbittorrent_service = AsyncMock()
    qbittorrent_service.add_torrent.return_value = "abc123"
    mock_qb_cls.return_value = qbittorrent_service
    mock_db.commit = AsyncMock()

    staging = StagingService(mock_db)
    result = await staging.use_releases(
        request_record,
        [selected_release],
        selection_source="manual",
    )

    assert result["status"] == "downloading"
    assert result["torrent_hashes"] == ["abc123"]
    qbittorrent_service.add_torrent.assert_awaited_once()
    queue_service.remove_from_queue.assert_awaited_once_with(request_record.id)


@patch.object(svc, "PendingQueueService")
@patch.object(svc, "get_settings")
@pytest.mark.asyncio
async def test_use_releases_tv_single_episode_selection_only_replaces_same_episode_stage(
    mock_get_settings, mock_pq_cls, mock_db
):
    settings = MagicMock(staging_mode_enabled=True)
    mock_get_settings.return_value = settings
    queue_service = AsyncMock()
    mock_pq_cls.return_value = queue_service

    request_record = MagicMock()
    request_record.id = 9
    request_record.media_type = MediaType.TV
    request_record.status = RequestStatus.PENDING

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
    episode_one_release.uploaded_by = None

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
    episode_two_release.uploaded_by = None

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
    reselection_release.uploaded_by = None

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
        active_result_after_episode_one,
    ]

    with patch.object(StagingService, "save_release", new_callable=AsyncMock) as mock_save:
        mock_save.side_effect = [
            stage_episode_one,
            stage_episode_two,
            stage_episode_one_replacement,
        ]

        staging = StagingService(mock_db)

        first_result = await staging.use_releases(
            request_record,
            [episode_one_release],
            selection_source="manual",
        )
        second_result = await staging.use_releases(
            request_record,
            [episode_two_release],
            selection_source="manual",
        )
        reselection_result = await staging.use_releases(
            request_record,
            [reselection_release],
            selection_source="manual",
        )

    assert first_result["action"] == "manual_staged"
    assert second_result["action"] == "manual_staged"
    assert reselection_result["status"] == "staged"
    assert reselection_result["action"] == "replaced_active_selection"
    assert reselection_result["staged_ids"] == [stage_episode_one_replacement.id]
    mock_db.delete.assert_awaited_with(stage_episode_one)
    assert stage_episode_two.status == "staged"
    assert mock_save.await_count == 3
    queue_service.remove_from_queue.assert_awaited()


def _tv_request(rid: int = 9) -> MagicMock:
    r = MagicMock()
    r.id = rid
    r.media_type = MediaType.TV
    r.status = RequestStatus.PENDING
    return r


def _release(
    rid: int,
    title: str,
    score: int = 50,
    size: int = 1_000,
    indexer: str = "Indexer A",
) -> MagicMock:
    r = MagicMock()
    r.id = rid
    r.title = title
    r.score = score
    r.size = size
    r.seeders = 10
    r.leechers = 1
    r.indexer = indexer
    r.magnet_url = f"magnet:?xt=urn:btih:{rid}"
    r.download_url = f"https://example.com/{rid}.torrent"
    r.info_hash = None
    r.publish_date = None
    r.resolution = None
    r.codec = None
    r.release_group = None
    r.uploaded_by = None
    return r


def _staged(
    sid: int,
    request_id: int,
    title: str,
    size: int = 1_000,
    indexer: str = "Indexer A",
) -> StagedTorrent:
    return StagedTorrent(
        id=sid,
        request_id=request_id,
        torrent_path=f"/tmp/{sid}.torrent",
        json_path=f"/tmp/{sid}.json",
        original_filename=f"s{sid}",
        title=title,
        size=size,
        indexer=indexer,
        score=50,
        status="staged",
        selection_source="manual",
    )


def _active_mock(staged: list[StagedTorrent]) -> MagicMock:
    m = MagicMock()
    m.scalars.return_value.all.return_value = staged
    return m


@patch.object(svc, "PendingQueueService")
@patch.object(svc, "get_settings")
@pytest.mark.asyncio
async def test_use_releases_tv_season_pack_s02_does_not_replace_s01(
    mock_get_settings, mock_pq_cls, mock_db
):
    """Season-1 pack staged, then Season-2 pack staged → both remain (no superseding)."""
    settings = MagicMock(staging_mode_enabled=True)
    mock_get_settings.return_value = settings
    queue_service = AsyncMock()
    mock_pq_cls.return_value = queue_service

    request_record = _tv_request()
    s01_release = _release(201, "Show.S01.1080p.WEB-DL")
    s02_release = _release(202, "Show.S02.1080p.WEB-DL")
    stage_s01 = _staged(71, request_record.id, "Show.S01.1080p.WEB-DL")
    stage_s02 = _staged(72, request_record.id, "Show.S02.1080p.WEB-DL")

    mock_db.execute.side_effect = [
        _active_mock([]),  # first call: nothing yet
        _active_mock([stage_s01]),  # second call: S01 already staged
    ]

    with patch.object(StagingService, "save_release", new_callable=AsyncMock) as mock_save:
        mock_save.side_effect = [stage_s01, stage_s02]
        staging = StagingService(mock_db)

        result1 = await staging.use_releases(
            request_record, [s01_release], selection_source="manual"
        )
        result2 = await staging.use_releases(
            request_record, [s02_release], selection_source="manual"
        )

    assert result1["action"] == "manual_staged"
    assert result2["action"] == "manual_staged"
    assert result2["status"] == "staged"
    assert mock_save.await_count == 2
    # No torrents were superseded — both should remain
    mock_db.delete.assert_not_called()
    queue_service.remove_from_queue.assert_awaited()


@patch.object(svc, "PendingQueueService")
@patch.object(svc, "get_settings")
@pytest.mark.asyncio
async def test_use_releases_tv_season_pack_replaces_same_season_keeps_other(
    mock_get_settings, mock_pq_cls, mock_db
):
    """S01 pack staged then a different S01 pack staged → old S01 replaced but S02 preserved."""
    settings = MagicMock(staging_mode_enabled=True)
    mock_get_settings.return_value = settings
    queue_service = AsyncMock()
    mock_pq_cls.return_value = queue_service

    request_record = _tv_request()
    s01_first = _release(301, "Show.S01.1080p.WEB-DL")
    s01_repack = _release(302, "Show.S01.REPACK.1080p.WEB-DL")
    s02_release = _release(303, "Show.S02.1080p.WEB-DL")
    stage_s01_first = _staged(81, request_record.id, "Show.S01.1080p.WEB-DL")
    stage_s02 = _staged(82, request_record.id, "Show.S02.1080p.WEB-DL")
    stage_s01_repack = _staged(83, request_record.id, "Show.S01.REPACK.1080p.WEB-DL")

    mock_db.execute.side_effect = [
        _active_mock([]),  # call 1: stage S01 → nothing active
        _active_mock([stage_s01_first]),  # call 2: stage S02 → S01 active
        _active_mock([stage_s01_first, stage_s02]),  # call 3: stage S01.REPACK → both active
    ]

    with patch.object(StagingService, "save_release", new_callable=AsyncMock) as mock_save:
        mock_save.side_effect = [stage_s01_first, stage_s02, stage_s01_repack]
        staging = StagingService(mock_db)

        await staging.use_releases(request_record, [s01_first], selection_source="manual")
        await staging.use_releases(request_record, [s02_release], selection_source="manual")
        result3 = await staging.use_releases(
            request_record, [s01_repack], selection_source="manual"
        )

    assert result3["action"] == "replaced_active_selection"
    assert result3["status"] == "staged"
    # stage_s01_first should be deleted (replaced), stage_s02 should be preserved
    mock_db.delete.assert_awaited_with(stage_s01_first)
    assert stage_s02.status == "staged"
    assert mock_save.await_count == 3


@patch.object(svc, "PendingQueueService")
@patch.object(svc, "get_settings")
@pytest.mark.asyncio
async def test_use_releases_tv_single_episode_does_not_interfere_with_season_pack(
    mock_get_settings, mock_pq_cls, mock_db
):
    """Single episode and season pack staged together → no interference."""
    settings = MagicMock(staging_mode_enabled=True)
    mock_get_settings.return_value = settings
    queue_service = AsyncMock()
    mock_pq_cls.return_value = queue_service

    request_record = _tv_request()
    s02_pack = _release(401, "Show.S02.1080p.WEB-DL")
    s01e01 = _release(402, "Show.S01E01.1080p.WEB-DL")
    stage_s02_pack = _staged(91, request_record.id, "Show.S02.1080p.WEB-DL")
    stage_s01e01 = _staged(92, request_record.id, "Show.S01E01.1080p.WEB-DL")

    mock_db.execute.side_effect = [
        _active_mock([]),
        _active_mock([stage_s02_pack]),
    ]

    with patch.object(StagingService, "save_release", new_callable=AsyncMock) as mock_save:
        mock_save.side_effect = [stage_s02_pack, stage_s01e01]
        staging = StagingService(mock_db)

        result1 = await staging.use_releases(request_record, [s02_pack], selection_source="manual")
        result2 = await staging.use_releases(request_record, [s01e01], selection_source="manual")

    assert result1["action"] == "manual_staged"
    assert result2["action"] == "manual_staged"
    assert mock_save.await_count == 2
    mock_db.delete.assert_not_called()


@patch.object(svc, "PendingQueueService")
@patch.object(svc, "get_settings")
@pytest.mark.asyncio
async def test_use_releases_tv_complete_series_replaces_all(
    mock_get_settings, mock_pq_cls, mock_db
):
    """Complete series staged → replaces all previously staged TV items."""
    settings = MagicMock(staging_mode_enabled=True)
    mock_get_settings.return_value = settings
    queue_service = AsyncMock()
    mock_pq_cls.return_value = queue_service

    request_record = _tv_request()
    s01_pack = _release(501, "Show.S01.1080p.WEB-DL")
    complete = _release(502, "Show.Complete.Series.1080p.WEB-DL")
    stage_s01_pack = _staged(101, request_record.id, "Show.S01.1080p.WEB-DL")
    stage_complete = _staged(102, request_record.id, "Show.Complete.Series.1080p.WEB-DL")

    mock_db.execute.side_effect = [
        _active_mock([]),
        _active_mock([stage_s01_pack]),
    ]

    with patch.object(StagingService, "save_release", new_callable=AsyncMock) as mock_save:
        mock_save.side_effect = [stage_s01_pack, stage_complete]
        staging = StagingService(mock_db)

        result1 = await staging.use_releases(request_record, [s01_pack], selection_source="manual")
        result2 = await staging.use_releases(request_record, [complete], selection_source="manual")

    assert result1["action"] == "manual_staged"
    assert result2["action"] == "replaced_active_selection"
    assert result2["status"] == "staged"
    mock_db.delete.assert_awaited_with(stage_s01_pack)
    assert mock_save.await_count == 2
