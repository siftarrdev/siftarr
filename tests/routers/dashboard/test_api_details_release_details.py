"""Tests for dashboard request detail payloads."""

import json
from datetime import UTC, datetime, timedelta
from typing import cast
from unittest.mock import MagicMock

import pytest

from app.siftarr.models.release import Release
from app.siftarr.models.request import MediaType, RequestStatus
from app.siftarr.routers import dashboard_api


@pytest.mark.asyncio
async def test_request_details_reuses_persisted_multi_season_coverage(
    mock_db, monkeypatch, background_tasks
):
    """Stored multi-season coverage should serialize and group by each covered season."""
    request_record = MagicMock()
    request_record.id = 21
    request_record.media_type = MediaType.TV
    request_record.status = RequestStatus.PENDING
    request_record.title = "Foundation"
    request_record.overseerr_request_id = None

    stored_release = Release(
        id=8,
        request_id=21,
        title="Foundation.S01-S02.2160p.WEB-DL",
        size=30 * 1024 * 1024 * 1024,
        seeders=55,
        leechers=4,
        download_url="https://example.test/foundation-s01-s02",
        magnet_url=None,
        info_hash=None,
        indexer="IndexerA",
        publish_date=None,
        resolution="2160p",
        codec=None,
        release_group=None,
        season_number=1,
        episode_number=None,
        season_coverage="1,2",
        score=95,
        passed_rules=True,
    )

    season_one = MagicMock(id=101, season_number=1, status=RequestStatus.PENDING, synced_at=None)
    season_two = MagicMock(id=102, season_number=2, status=RequestStatus.PENDING, synced_at=None)

    request_result = MagicMock()
    request_result.scalar_one_or_none.return_value = request_record
    release_result = MagicMock()
    release_result.scalars.return_value.all.return_value = [stored_release]
    rules_result = MagicMock()
    rules_result.scalars.return_value.all.return_value = []
    seasons_result = MagicMock()
    seasons_result.scalars.return_value.all.return_value = [season_one, season_two]
    episodes_one_result = MagicMock()
    episodes_one_result.scalars.return_value.all.return_value = []
    episodes_two_result = MagicMock()
    episodes_two_result.scalars.return_value.all.return_value = []
    mock_db.execute.side_effect = [
        request_result,
        release_result,
        rules_result,
        seasons_result,
        episodes_one_result,
        episodes_two_result,
    ]

    monkeypatch.setattr(dashboard_api, "get_settings", lambda: MagicMock())

    class FakeOverseerrService:
        def __init__(self, settings):
            pass

        async def close(self):
            return None

    class FakePlexService:
        async def close(self):
            return None

    fake_engine = MagicMock()
    fake_engine.evaluate.return_value = MagicMock(rejection_reason=None, matches=[])
    fake_engine.evaluate_per_season_size.return_value = True

    monkeypatch.setattr(dashboard_api, "OverseerrService", FakeOverseerrService)
    monkeypatch.setattr(dashboard_api, "PlexService", lambda settings: FakePlexService())
    monkeypatch.setattr(
        dashboard_api.RuleEngine,
        "from_db_rules",
        MagicMock(return_value=fake_engine),
    )

    class FakeEpisodeSyncService:
        def __init__(self, db, plex):
            self.db = db
            self.plex = plex

        async def sync_request(self, request_id):
            return None

    with pytest.MonkeyPatch.context() as inner_monkeypatch:
        inner_monkeypatch.setattr(
            "app.siftarr.services.episode_sync_service.EpisodeSyncService",
            FakeEpisodeSyncService,
        )
        response = await dashboard_api.request_details(
            request_id=21, background_tasks=background_tasks, db=mock_db
        )

    body = json.loads(cast(bytes, response.body))
    assert body["releases"][0]["covered_seasons"] == [1, 2]
    assert body["releases"][0]["covered_season_count"] == 2
    assert body["releases"][0]["covers_all_known_seasons"] is True
    assert body["releases"][0]["size_per_season"] == "15.00 GB"
    assert [release["title"] for release in body["tv_info"]["releases_by_season"]["1"]] == [
        "Foundation.S01-S02.2160p.WEB-DL"
    ]
    assert [release["title"] for release in body["tv_info"]["releases_by_season"]["2"]] == [
        "Foundation.S01-S02.2160p.WEB-DL"
    ]


@pytest.mark.asyncio
async def test_request_details_orders_stored_releases_by_score_then_size(
    mock_db, monkeypatch, background_tasks
):
    """Stored releases should use the same score-desc, size-asc ordering."""
    request_record = MagicMock()
    request_record.id = 21
    request_record.media_type = MediaType.TV
    request_record.status = RequestStatus.PENDING
    request_record.title = "Foundation"
    request_record.overseerr_request_id = None

    larger_high_score = Release(
        id=8,
        request_id=21,
        title="Foundation.S01-S02.2160p.WEB-DL",
        size=30 * 1024 * 1024 * 1024,
        seeders=55,
        leechers=4,
        download_url="https://example.test/foundation-large",
        magnet_url=None,
        info_hash=None,
        indexer="IndexerA",
        publish_date=None,
        resolution="2160p",
        codec=None,
        release_group=None,
        season_number=1,
        episode_number=None,
        season_coverage="1,2",
        score=95,
        passed_rules=True,
    )
    lower_score = Release(
        id=9,
        request_id=21,
        title="Foundation.S01.720p.WEB-DL",
        size=10 * 1024 * 1024 * 1024,
        seeders=99,
        leechers=1,
        download_url="https://example.test/foundation-low-score",
        magnet_url=None,
        info_hash=None,
        indexer="IndexerB",
        publish_date=None,
        resolution="720p",
        codec=None,
        release_group=None,
        season_number=1,
        episode_number=None,
        season_coverage="1",
        score=90,
        passed_rules=True,
    )
    smaller_high_score = Release(
        id=10,
        request_id=21,
        title="Foundation.S01-02.1080p.WEB-DL",
        size=20 * 1024 * 1024 * 1024,
        seeders=22,
        leechers=2,
        download_url="https://example.test/foundation-small",
        magnet_url=None,
        info_hash=None,
        indexer="IndexerC",
        publish_date=None,
        resolution="1080p",
        codec=None,
        release_group=None,
        season_number=1,
        episode_number=None,
        season_coverage="1,2",
        score=95,
        passed_rules=True,
    )

    season_one = MagicMock(id=101, season_number=1, status=RequestStatus.PENDING, synced_at=None)
    season_two = MagicMock(id=102, season_number=2, status=RequestStatus.PENDING, synced_at=None)

    request_result = MagicMock()
    request_result.scalar_one_or_none.return_value = request_record
    release_result = MagicMock()
    release_result.scalars.return_value.all.return_value = [
        larger_high_score,
        lower_score,
        smaller_high_score,
    ]
    rules_result = MagicMock()
    rules_result.scalars.return_value.all.return_value = []
    seasons_result = MagicMock()
    seasons_result.scalars.return_value.all.return_value = [season_one, season_two]
    episodes_one_result = MagicMock()
    episodes_one_result.scalars.return_value.all.return_value = []
    episodes_two_result = MagicMock()
    episodes_two_result.scalars.return_value.all.return_value = []
    mock_db.execute.side_effect = [
        request_result,
        release_result,
        rules_result,
        seasons_result,
        episodes_one_result,
        episodes_two_result,
    ]

    monkeypatch.setattr(dashboard_api, "get_settings", lambda: MagicMock())

    class FakeOverseerrService:
        def __init__(self, settings):
            pass

        async def close(self):
            return None

    class FakePlexService:
        async def close(self):
            return None

    fake_engine = MagicMock()
    fake_engine.evaluate.return_value = MagicMock(rejection_reason=None, matches=[])
    fake_engine.evaluate_per_season_size.return_value = True

    monkeypatch.setattr(dashboard_api, "OverseerrService", FakeOverseerrService)
    monkeypatch.setattr(dashboard_api, "PlexService", lambda settings: FakePlexService())
    monkeypatch.setattr(
        dashboard_api.RuleEngine,
        "from_db_rules",
        MagicMock(return_value=fake_engine),
    )

    class FakeEpisodeSyncService:
        def __init__(self, db, plex):
            self.db = db
            self.plex = plex

        async def sync_request(self, request_id):
            return None

    with pytest.MonkeyPatch.context() as inner_monkeypatch:
        inner_monkeypatch.setattr(
            "app.siftarr.services.episode_sync_service.EpisodeSyncService",
            FakeEpisodeSyncService,
        )
        response = await dashboard_api.request_details(
            request_id=21, background_tasks=background_tasks, db=mock_db
        )

    body = json.loads(cast(bytes, response.body))
    assert [release["title"] for release in body["releases"]] == [
        "Foundation.S01-02.1080p.WEB-DL",
        "Foundation.S01-S02.2160p.WEB-DL",
        "Foundation.S01.720p.WEB-DL",
    ]
    assert [release["title"] for release in body["tv_info"]["releases_by_season"]["1"]] == [
        "Foundation.S01-02.1080p.WEB-DL",
        "Foundation.S01-S02.2160p.WEB-DL",
        "Foundation.S01.720p.WEB-DL",
    ]
    assert [release["title"] for release in body["tv_info"]["releases_by_season"]["2"]] == [
        "Foundation.S01-02.1080p.WEB-DL",
        "Foundation.S01-S02.2160p.WEB-DL",
    ]
    assert all("_size_bytes" not in release for release in body["releases"])


@pytest.mark.asyncio
async def test_request_details_includes_release_status_reason_and_publish_date(
    mock_db, monkeypatch, background_tasks
):
    """Stored request details should surface status metadata needed by the UI."""
    request_record = MagicMock()
    request_record.id = 21
    request_record.media_type = MediaType.MOVIE
    request_record.status = RequestStatus.PENDING
    request_record.title = "Foundation"
    request_record.overseerr_request_id = None

    published_at = datetime.now(UTC) - timedelta(days=2)
    stored_release = Release(
        id=8,
        request_id=21,
        title="Foundation.2160p.WEB-DL",
        size=30 * 1024 * 1024 * 1024,
        seeders=55,
        leechers=4,
        download_url="https://example.test/foundation",
        magnet_url=None,
        info_hash=None,
        indexer="IndexerA",
        publish_date=published_at,
        resolution="2160p",
        codec=None,
        release_group=None,
        season_number=None,
        episode_number=None,
        season_coverage=None,
        score=95,
        passed_rules=False,
    )

    request_result = MagicMock()
    request_result.scalar_one_or_none.return_value = request_record
    release_result = MagicMock()
    release_result.scalars.return_value.all.return_value = [stored_release]
    rules_result = MagicMock()
    rules_result.scalars.return_value.all.return_value = []
    mock_db.execute.side_effect = [request_result, release_result, rules_result]

    monkeypatch.setattr(dashboard_api, "get_settings", lambda: MagicMock())

    class FakeOverseerrService:
        def __init__(self, settings):
            pass

        async def close(self):
            return None

    fake_engine = MagicMock()
    fake_engine.evaluate.return_value = MagicMock(
        rejection_reason="Blocked by quality profile",
        matches=[],
        total_score=95,
        passed=False,
    )

    monkeypatch.setattr(dashboard_api, "OverseerrService", FakeOverseerrService)
    monkeypatch.setattr(
        dashboard_api.RuleEngine,
        "from_db_rules",
        MagicMock(return_value=fake_engine),
    )

    response = await dashboard_api.request_details(
        request_id=21, background_tasks=background_tasks, db=mock_db
    )

    body = json.loads(cast(bytes, response.body))
    assert body["releases"][0]["id"] == 8
    assert body["releases"][0]["stored_release_id"] == 8
    assert body["releases"][0]["status"] == "rejected"
    assert body["releases"][0]["status_label"] == "Rejected"
    assert body["releases"][0]["rejection_reason"] == "Blocked by quality profile"
    assert body["releases"][0]["publish_date"] == published_at.isoformat()
