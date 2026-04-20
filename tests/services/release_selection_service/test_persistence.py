from unittest.mock import AsyncMock, MagicMock

import pytest

from app.siftarr.models.release import Release
from app.siftarr.services import release_selection_service
from app.siftarr.services.prowlarr_service import ProwlarrRelease
from app.siftarr.services.rule_engine import ReleaseEvaluation


@pytest.mark.asyncio
async def test_persist_manual_release_upserts_existing_release(mock_db, request_record):
    existing_release = Release(
        id=1,
        request_id=request_record.id,
        title="User Pick",
        size=1,
        seeders=1,
        leechers=0,
        download_url="https://old.example/release.torrent",
        indexer="OldIndexer",
        score=1,
        passed_rules=False,
    )
    query_result = MagicMock()
    query_result.scalar_one_or_none.return_value = existing_release
    mock_db.execute.return_value = query_result
    mock_db.commit = AsyncMock()
    mock_db.refresh = AsyncMock()

    release = ProwlarrRelease(
        title="User Pick",
        size=10,
        seeders=25,
        leechers=3,
        download_url="https://example.com/user-pick.torrent",
        magnet_url="magnet:?xt=urn:btih:userpick",
        info_hash="abc123",
        indexer="Indexer A",
    )
    evaluation = ReleaseEvaluation(release=release, passed=True, total_score=50, matches=[])

    stored = await release_selection_service.persist_manual_release(
        mock_db,
        request_record,
        release,
        evaluation,
    )

    assert stored is existing_release
    assert existing_release.download_url == release.download_url
    assert existing_release.magnet_url == release.magnet_url
    assert existing_release.info_hash == release.info_hash
    assert existing_release.score == 50
    assert existing_release.passed_rules is True
    mock_db.commit.assert_awaited_once()
    mock_db.refresh.assert_awaited_once_with(existing_release)


@pytest.mark.asyncio
async def test_persist_manual_release_requires_download_source(mock_db, request_record):
    release = ProwlarrRelease(
        title="User Pick",
        size=10,
        seeders=1,
        leechers=0,
        download_url="",
        magnet_url=None,
        indexer="Indexer A",
    )
    evaluation = ReleaseEvaluation(release=release, passed=True, total_score=10, matches=[])

    with pytest.raises(RuntimeError, match="no usable download source"):
        await release_selection_service.persist_manual_release(
            mock_db,
            request_record,
            release,
            evaluation,
        )


@pytest.mark.asyncio
async def test_store_search_results_persists_multi_season_coverage(mock_db):
    mock_db.add = MagicMock()
    mock_db.commit = AsyncMock()
    mock_db.refresh = AsyncMock()

    release = ProwlarrRelease(
        title="Show.S01-S03.2160p.WEB-DL",
        size=30 * 1024 * 1024 * 1024,
        seeders=50,
        leechers=4,
        download_url="https://example.test/show-s01-s03.torrent",
        indexer="IndexerA",
    )
    evaluation = ReleaseEvaluation(release=release, passed=True, total_score=95, matches=[])

    await release_selection_service.store_search_results(mock_db, 12, [evaluation])

    stored_record = mock_db.add.call_args.args[0]
    assert stored_record.request_id == 12
    assert stored_record.season_number == 1
    assert stored_record.episode_number is None
    assert stored_record.season_coverage == "1,2,3"
    mock_db.commit.assert_awaited_once()
    mock_db.refresh.assert_awaited_once_with(stored_record)


@pytest.mark.asyncio
async def test_store_search_results_persists_complete_series_marker(mock_db):
    mock_db.add = MagicMock()
    mock_db.commit = AsyncMock()
    mock_db.refresh = AsyncMock()

    release = ProwlarrRelease(
        title="Show.Complete.Series.1080p.BluRay",
        size=42 * 1024 * 1024 * 1024,
        seeders=77,
        leechers=2,
        download_url="https://example.test/show-complete-series.torrent",
        indexer="IndexerB",
    )
    evaluation = ReleaseEvaluation(release=release, passed=True, total_score=88, matches=[])

    await release_selection_service.store_search_results(mock_db, 33, [evaluation])

    stored_record = mock_db.add.call_args.args[0]
    assert stored_record.request_id == 33
    assert stored_record.season_number is None
    assert stored_record.episode_number is None
    assert stored_record.season_coverage == "*"
