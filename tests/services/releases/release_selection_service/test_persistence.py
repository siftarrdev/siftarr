from unittest.mock import AsyncMock, MagicMock

import pytest

from app.siftarr.services.decisions.rule_engine import ReleaseEvaluation
from app.siftarr.services.integrations.prowlarr_service import ProwlarrRelease
from app.siftarr.services.releases import release_storage


@pytest.mark.asyncio
async def test_store_search_results_persists_multi_season_coverage(mock_db):
    mock_db.add = MagicMock()
    mock_db.commit = AsyncMock()
    # Set up execute to return no existing records (upsert path)
    scalars_mock = MagicMock()
    scalars_mock.all.return_value = []
    mock_db.execute = AsyncMock(
        return_value=MagicMock(scalars=MagicMock(return_value=scalars_mock))
    )

    release = ProwlarrRelease(
        title="Show.S01-S03.2160p.WEB-DL",
        size=30 * 1024 * 1024 * 1024,
        seeders=50,
        leechers=4,
        download_url="https://example.test/show-s01-s03.torrent",
        indexer="IndexerA",
    )
    evaluation = ReleaseEvaluation(release=release, passed=True, total_score=95, matches=[])

    await release_storage.store_search_results(mock_db, 12, [evaluation])

    stored_record = mock_db.add.call_args.args[0]
    assert stored_record.request_id == 12
    assert stored_record.season_number == 1
    assert stored_record.episode_number is None
    assert stored_record.season_coverage == "1,2,3"
    mock_db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_store_search_results_persists_complete_series_marker(mock_db):
    mock_db.add = MagicMock()
    mock_db.commit = AsyncMock()
    # Set up execute to return no existing records (upsert path)
    scalars_mock = MagicMock()
    scalars_mock.all.return_value = []
    mock_db.execute = AsyncMock(
        return_value=MagicMock(scalars=MagicMock(return_value=scalars_mock))
    )

    release = ProwlarrRelease(
        title="Show.Complete.Series.1080p.BluRay",
        size=42 * 1024 * 1024 * 1024,
        seeders=77,
        leechers=2,
        download_url="https://example.test/show-complete-series.torrent",
        indexer="IndexerB",
    )
    evaluation = ReleaseEvaluation(release=release, passed=True, total_score=88, matches=[])

    await release_storage.store_search_results(mock_db, 33, [evaluation])

    stored_record = mock_db.add.call_args.args[0]
    assert stored_record.request_id == 33
    assert stored_record.season_number is None
    assert stored_record.episode_number is None
    assert stored_record.season_coverage == "*"
