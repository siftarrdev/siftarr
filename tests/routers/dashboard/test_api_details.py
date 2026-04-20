"""Tests for dashboard detail API routes."""

import json
from datetime import UTC, datetime, timedelta
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.siftarr.models.release import Release
from app.siftarr.models.request import MediaType, RequestStatus
from app.siftarr.routers import dashboard_api
from app.siftarr.services import tv_details_service


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
        is_downloaded=False,
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

    monkeypatch.setattr(dashboard_api, "get_effective_settings", AsyncMock(return_value=MagicMock()))

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

        async def refresh_if_stale(self, request_id):
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
        is_downloaded=False,
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
        is_downloaded=False,
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
        is_downloaded=False,
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

    monkeypatch.setattr(dashboard_api, "get_effective_settings", AsyncMock(return_value=MagicMock()))

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

        async def refresh_if_stale(self, request_id):
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
        is_downloaded=False,
    )

    request_result = MagicMock()
    request_result.scalar_one_or_none.return_value = request_record
    release_result = MagicMock()
    release_result.scalars.return_value.all.return_value = [stored_release]
    rules_result = MagicMock()
    rules_result.scalars.return_value.all.return_value = []
    mock_db.execute.side_effect = [request_result, release_result, rules_result]

    monkeypatch.setattr(dashboard_api, "get_effective_settings", AsyncMock(return_value=MagicMock()))

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


@pytest.mark.asyncio
async def test_request_details_surfaces_active_staged_torrent_metadata(
    mock_db, monkeypatch, background_tasks
):
    """Request details should mark the current active staged torrent for replacement UX."""
    request_record = MagicMock()
    request_record.id = 21
    request_record.media_type = MediaType.MOVIE
    request_record.status = RequestStatus.STAGED
    request_record.title = "Foundation"
    request_record.overseerr_request_id = None

    active_release = Release(
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
        publish_date=None,
        resolution="2160p",
        codec=None,
        release_group=None,
        season_number=None,
        episode_number=None,
        season_coverage=None,
        score=95,
        passed_rules=True,
        is_downloaded=False,
    )
    other_release = Release(
        id=9,
        request_id=21,
        title="Foundation.1080p.WEB-DL",
        size=20 * 1024 * 1024 * 1024,
        seeders=65,
        leechers=2,
        download_url="https://example.test/foundation-1080p",
        magnet_url=None,
        info_hash=None,
        indexer="IndexerB",
        publish_date=None,
        resolution="1080p",
        codec=None,
        release_group=None,
        season_number=None,
        episode_number=None,
        season_coverage=None,
        score=90,
        passed_rules=True,
        is_downloaded=False,
    )
    active_stage = MagicMock()
    active_stage.id = 77
    active_stage.title = active_release.title
    active_stage.status = "staged"
    active_stage.selection_source = "rule"

    request_result = MagicMock()
    request_result.scalar_one_or_none.return_value = request_record
    release_result = MagicMock()
    release_result.scalars.return_value.all.return_value = [active_release, other_release]
    rules_result = MagicMock()
    rules_result.scalars.return_value.all.return_value = []
    active_stage_result = MagicMock()
    active_stage_result.scalars.return_value.all.return_value = [active_stage]
    mock_db.execute.side_effect = [request_result, release_result, rules_result, active_stage_result]

    monkeypatch.setattr(dashboard_api, "get_effective_settings", AsyncMock(return_value=MagicMock()))

    class FakeOverseerrService:
        def __init__(self, settings):
            pass

        async def close(self):
            return None

    fake_engine = MagicMock()
    fake_engine.evaluate.return_value = MagicMock(
        rejection_reason=None,
        matches=[],
        total_score=95,
        passed=True,
    )
    fake_engine.evaluate_per_season_size.return_value = True

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
    assert body["active_staged_torrent"] == {
        "id": 77,
        "title": active_release.title,
        "status": "staged",
        "selection_source": "rule",
        "target_scope": {"type": "request"},
    }
    assert body["active_staged_torrents"] == [body["active_staged_torrent"]]
    assert body["releases"][0]["is_active_selection"] is True
    assert body["releases"][0]["active_selection_source"] == "rule"
    assert body["releases"][0]["target_scope"] == {"type": "request"}
    assert body["releases"][0]["active_staged_torrent"] == body["active_staged_torrent"]
    assert body["releases"][1]["is_active_selection"] is False


@pytest.mark.asyncio
async def test_request_details_tv_scopes_active_stage_to_matching_episode(
    mock_db, monkeypatch, background_tasks
):
    """TV details should expose per-episode staged metadata instead of request-wide flags."""
    request_record = MagicMock()
    request_record.id = 21
    request_record.media_type = MediaType.TV
    request_record.status = RequestStatus.STAGED
    request_record.title = "Foundation"
    request_record.overseerr_request_id = None

    episode_one_release = Release(
        id=8,
        request_id=21,
        title="Foundation.S01E01.1080p.WEB-DL",
        size=2 * 1024 * 1024 * 1024,
        seeders=55,
        leechers=4,
        download_url="https://example.test/foundation-s01e01",
        magnet_url=None,
        info_hash=None,
        indexer="IndexerA",
        publish_date=None,
        resolution="1080p",
        codec=None,
        release_group=None,
        season_number=1,
        episode_number=1,
        season_coverage=None,
        score=95,
        passed_rules=True,
        is_downloaded=False,
    )
    episode_two_release = Release(
        id=9,
        request_id=21,
        title="Foundation.S01E02.1080p.WEB-DL",
        size=2 * 1024 * 1024 * 1024,
        seeders=45,
        leechers=2,
        download_url="https://example.test/foundation-s01e02",
        magnet_url=None,
        info_hash=None,
        indexer="IndexerB",
        publish_date=None,
        resolution="1080p",
        codec=None,
        release_group=None,
        season_number=1,
        episode_number=2,
        season_coverage=None,
        score=90,
        passed_rules=True,
        is_downloaded=False,
    )

    active_episode_one_stage = MagicMock()
    active_episode_one_stage.id = 77
    active_episode_one_stage.title = episode_one_release.title
    active_episode_one_stage.status = "staged"
    active_episode_one_stage.selection_source = "manual"

    season_one = MagicMock(id=101, season_number=1, status=RequestStatus.PENDING, synced_at=None)
    episode_one = MagicMock(
        id=201,
        season_id=101,
        episode_number=1,
        title="Episode 1",
        air_date=None,
        status=RequestStatus.PENDING,
        release_id=8,
    )
    episode_two = MagicMock(
        id=202,
        season_id=101,
        episode_number=2,
        title="Episode 2",
        air_date=None,
        status=RequestStatus.PENDING,
        release_id=9,
    )

    request_result = MagicMock()
    request_result.scalar_one_or_none.return_value = request_record
    release_result = MagicMock()
    release_result.scalars.return_value.all.return_value = [episode_one_release, episode_two_release]
    rules_result = MagicMock()
    rules_result.scalars.return_value.all.return_value = []
    active_stage_result = MagicMock()
    active_stage_result.scalars.return_value.all.return_value = [active_episode_one_stage]
    seasons_result = MagicMock()
    seasons_result.scalars.return_value.all.return_value = [season_one]
    episodes_result = MagicMock()
    episodes_result.scalars.return_value.all.return_value = [episode_one, episode_two]
    mock_db.execute.side_effect = [
        request_result,
        release_result,
        rules_result,
        active_stage_result,
        seasons_result,
        episodes_result,
    ]

    monkeypatch.setattr(dashboard_api, "get_effective_settings", AsyncMock(return_value=MagicMock()))

    class FakeOverseerrService:
        def __init__(self, settings):
            pass

        async def close(self):
            return None

    fake_engine = MagicMock()
    fake_engine.evaluate.return_value = MagicMock(
        rejection_reason=None,
        matches=[],
        total_score=95,
        passed=True,
    )
    fake_engine.evaluate_per_season_size.return_value = True

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
    assert body["active_staged_torrents"] == [
        {
            "id": 77,
            "title": "Foundation.S01E01.1080p.WEB-DL",
            "status": "staged",
            "selection_source": "manual",
            "target_scope": {
                "type": "single_episode",
                "season_number": 1,
                "episode_number": 1,
            },
        }
    ]
    assert body["releases"][0]["target_scope"] == {
        "type": "single_episode",
        "season_number": 1,
        "episode_number": 1,
    }
    assert body["releases"][0]["is_active_selection"] is True
    assert body["releases"][0]["active_staged_torrent"] == body["active_staged_torrents"][0]
    assert body["releases"][1]["target_scope"] == {
        "type": "single_episode",
        "season_number": 1,
        "episode_number": 2,
    }
    assert body["releases"][1]["is_active_selection"] is False
    assert body["releases"][1]["active_staged_torrent"] is None


@pytest.mark.asyncio
async def test_request_details_returns_cached_tv_data_and_sync_state(
    mock_db, monkeypatch, background_tasks
):
    """TV details should return persisted seasons immediately and schedule refresh in background."""
    request_record = MagicMock()
    request_record.id = 21
    request_record.media_type = MediaType.TV
    request_record.status = RequestStatus.PENDING
    request_record.title = "Foundation"
    request_record.overseerr_request_id = None

    stale_synced_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=2)
    season_one = MagicMock(id=101, season_number=1, status=RequestStatus.PENDING, synced_at=stale_synced_at)
    episode_one = MagicMock(
        id=201,
        season_id=101,
        episode_number=1,
        title="Pilot",
        air_date=None,
        status=RequestStatus.PENDING,
        release_id=None,
    )

    request_result = MagicMock()
    request_result.scalar_one_or_none.return_value = request_record
    release_result = MagicMock()
    release_result.scalars.return_value.all.return_value = []
    rules_result = MagicMock()
    rules_result.scalars.return_value.all.return_value = []
    seasons_result = MagicMock()
    seasons_result.scalars.return_value.all.return_value = [season_one]
    episodes_result = MagicMock()
    episodes_result.scalars.return_value.all.return_value = [episode_one]
    mock_db.execute.side_effect = [
        request_result,
        release_result,
        rules_result,
        seasons_result,
        episodes_result,
    ]

    monkeypatch.setattr(dashboard_api, "get_effective_settings", AsyncMock(return_value=MagicMock()))

    class FakeOverseerrService:
        def __init__(self, settings):
            pass

        async def close(self):
            return None

    fake_engine = MagicMock()
    fake_engine.evaluate.return_value = MagicMock(rejection_reason=None, matches=[])
    scheduled = []
    monkeypatch.setattr(dashboard_api, "OverseerrService", FakeOverseerrService)
    monkeypatch.setattr(
        dashboard_api.RuleEngine,
        "from_db_rules",
        MagicMock(return_value=fake_engine),
    )
    monkeypatch.setattr(
        tv_details_service,
        "schedule_background_episode_refresh",
        lambda tasks, request_id: scheduled.append((tasks, request_id)) or True,
    )

    response = await dashboard_api.request_details(
        request_id=21, background_tasks=background_tasks, db=mock_db
    )

    body = json.loads(cast(bytes, response.body))
    assert body["tv_info"]["seasons"][0]["episodes"][0]["title"] == "Pilot"
    assert body["tv_info"]["sync_state"]["stale"] is True
    assert body["tv_info"]["sync_state"]["refresh_in_progress"] is True
    assert body["tv_info"]["sync_state"]["needs_plex_enrichment"] is True
    assert scheduled == [(background_tasks, 21)]


@pytest.mark.asyncio
async def test_get_request_seasons_returns_sync_state_without_inline_refresh(
    mock_db, monkeypatch, background_tasks
):
    """Season endpoint should return cached data and sync metadata without blocking refresh."""
    request_record = MagicMock()
    request_record.id = 21
    request_record.media_type = MediaType.TV

    synced_at = datetime.now(UTC).replace(tzinfo=None)
    season_one = MagicMock(id=101, season_number=1, status=RequestStatus.AVAILABLE, synced_at=synced_at)
    episode_one = MagicMock(
        id=201,
        season_id=101,
        episode_number=1,
        title="Pilot",
        air_date=None,
        status=RequestStatus.AVAILABLE,
        release_id=None,
    )

    request_result = MagicMock()
    request_result.scalar_one_or_none.return_value = request_record
    seasons_result = MagicMock()
    seasons_result.scalars.return_value.all.return_value = [season_one]
    episodes_result = MagicMock()
    episodes_result.scalars.return_value.all.return_value = [episode_one]
    mock_db.execute.side_effect = [request_result, seasons_result, episodes_result]

    scheduled = []
    monkeypatch.setattr(
        tv_details_service,
        "schedule_background_episode_refresh",
        lambda tasks, request_id: scheduled.append((tasks, request_id)) or True,
    )

    response = await dashboard_api.get_request_seasons(
        request_id=21, background_tasks=background_tasks, db=mock_db
    )

    body = json.loads(cast(bytes, response.body))
    assert body["seasons"][0]["episodes"][0]["status"] == RequestStatus.AVAILABLE.value
    assert body["sync_state"]["stale"] is False
    assert body["sync_state"]["refresh_in_progress"] is False
    assert scheduled == []


@pytest.mark.asyncio
async def test_request_details_serializes_unreleased_and_partial_tv_counts(
    mock_db, monkeypatch, background_tasks
):
    """TV details should preserve partial availability and unreleased episodes."""
    request_record = MagicMock()
    request_record.id = 21
    request_record.media_type = MediaType.TV
    request_record.status = RequestStatus.PENDING
    request_record.title = "The Rookie"
    request_record.overseerr_request_id = None

    synced_at = datetime.now(UTC).replace(tzinfo=None)
    season_one = MagicMock(
        id=101,
        season_number=8,
        status=RequestStatus.PARTIALLY_AVAILABLE,
        synced_at=synced_at,
    )
    available_episode = MagicMock(
        id=201,
        season_id=101,
        episode_number=15,
        title="Episode 15",
        air_date=None,
        status=RequestStatus.AVAILABLE,
        release_id=None,
    )
    future_episode = MagicMock(
        id=202,
        season_id=101,
        episode_number=16,
        title="Episode 16",
        air_date=(datetime.now(UTC) + timedelta(days=7)).date(),
        status=RequestStatus.UNRELEASED,
        release_id=None,
    )

    request_result = MagicMock()
    request_result.scalar_one_or_none.return_value = request_record
    release_result = MagicMock()
    release_result.scalars.return_value.all.return_value = []
    rules_result = MagicMock()
    rules_result.scalars.return_value.all.return_value = []
    seasons_result = MagicMock()
    seasons_result.scalars.return_value.all.return_value = [season_one]
    episodes_result = MagicMock()
    episodes_result.scalars.return_value.all.return_value = [available_episode, future_episode]
    mock_db.execute.side_effect = [
        request_result,
        release_result,
        rules_result,
        seasons_result,
        episodes_result,
    ]

    monkeypatch.setattr(dashboard_api, "get_effective_settings", AsyncMock(return_value=MagicMock()))

    class FakeOverseerrService:
        def __init__(self, settings):
            pass

        async def close(self):
            return None

    fake_engine = MagicMock()
    fake_engine.evaluate.return_value = MagicMock(rejection_reason=None, matches=[])
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
    season_payload = body["tv_info"]["seasons"][0]
    assert season_payload["status"] == RequestStatus.PARTIALLY_AVAILABLE.value
    assert season_payload["available_count"] == 1
    assert season_payload["pending_count"] == 0
    assert season_payload["unreleased_count"] == 1
    assert [episode["status"] for episode in season_payload["episodes"]] == [
        RequestStatus.AVAILABLE.value,
        RequestStatus.UNRELEASED.value,
    ]


@pytest.mark.asyncio
async def test_request_details_flags_fresh_partial_tv_data_for_plex_enrichment(
    mock_db, monkeypatch, background_tasks
):
    """Fresh partial seasons with 0 available episodes should trigger Plex enrichment."""
    request_record = MagicMock()
    request_record.id = 21
    request_record.media_type = MediaType.TV
    request_record.status = RequestStatus.PENDING
    request_record.title = "The Rookie"
    request_record.overseerr_request_id = None

    synced_at = datetime.now(UTC).replace(tzinfo=None)
    season_one = MagicMock(
        id=101,
        season_number=8,
        status=RequestStatus.PARTIALLY_AVAILABLE,
        synced_at=synced_at,
    )
    pending_episode = MagicMock(
        id=201,
        season_id=101,
        episode_number=1,
        title="Episode 1",
        air_date=None,
        status=RequestStatus.PENDING,
        release_id=None,
    )
    unreleased_episode = MagicMock(
        id=202,
        season_id=101,
        episode_number=2,
        title="Episode 2",
        air_date=(datetime.now(UTC) + timedelta(days=7)).date(),
        status=RequestStatus.UNRELEASED,
        release_id=None,
    )

    request_result = MagicMock()
    request_result.scalar_one_or_none.return_value = request_record
    release_result = MagicMock()
    release_result.scalars.return_value.all.return_value = []
    rules_result = MagicMock()
    rules_result.scalars.return_value.all.return_value = []
    seasons_result = MagicMock()
    seasons_result.scalars.return_value.all.return_value = [season_one]
    episodes_result = MagicMock()
    episodes_result.scalars.return_value.all.return_value = [pending_episode, unreleased_episode]
    mock_db.execute.side_effect = [
        request_result,
        release_result,
        rules_result,
        seasons_result,
        episodes_result,
    ]

    monkeypatch.setattr(dashboard_api, "get_effective_settings", AsyncMock(return_value=MagicMock()))

    class FakeOverseerrService:
        def __init__(self, settings):
            pass

        async def close(self):
            return None

    fake_engine = MagicMock()
    fake_engine.evaluate.return_value = MagicMock(rejection_reason=None, matches=[])
    scheduled = []
    monkeypatch.setattr(dashboard_api, "OverseerrService", FakeOverseerrService)
    monkeypatch.setattr(
        dashboard_api.RuleEngine,
        "from_db_rules",
        MagicMock(return_value=fake_engine),
    )
    monkeypatch.setattr(
        tv_details_service,
        "schedule_background_episode_refresh",
        lambda tasks, request_id: scheduled.append((tasks, request_id)) or True,
    )

    response = await dashboard_api.request_details(
        request_id=21, background_tasks=background_tasks, db=mock_db
    )

    body = json.loads(cast(bytes, response.body))
    season_payload = body["tv_info"]["seasons"][0]
    assert season_payload["status"] == RequestStatus.PARTIALLY_AVAILABLE.value
    assert season_payload["available_count"] == 0
    assert season_payload["total_count"] == 2
    assert season_payload["pending_count"] == 1
    assert season_payload["unreleased_count"] == 1
    assert body["tv_info"]["sync_state"]["stale"] is False
    assert body["tv_info"]["sync_state"]["needs_plex_enrichment"] is True
    assert body["tv_info"]["sync_state"]["refresh_in_progress"] is True
    assert scheduled == [(background_tasks, 21)]


@pytest.mark.asyncio
async def test_request_details_flags_pending_unreleased_tv_data_for_plex_enrichment(
    mock_db, monkeypatch, background_tasks
):
    """Unresolved pending/unreleased TV rows should still report Plex enrichment needed."""
    request_record = MagicMock()
    request_record.id = 51
    request_record.media_type = MediaType.TV
    request_record.status = RequestStatus.PENDING
    request_record.title = "The Rookie"
    request_record.overseerr_request_id = None

    synced_at = datetime.now(UTC).replace(tzinfo=None)
    season_one = MagicMock(
        id=101,
        season_number=8,
        status=RequestStatus.PENDING,
        synced_at=synced_at,
    )
    pending_episode = MagicMock(
        id=201,
        season_id=101,
        episode_number=1,
        title="Episode 1",
        air_date=None,
        status=RequestStatus.PENDING,
        release_id=None,
    )
    unreleased_episode = MagicMock(
        id=202,
        season_id=101,
        episode_number=2,
        title="Episode 2",
        air_date=(datetime.now(UTC) + timedelta(days=7)).date(),
        status=RequestStatus.UNRELEASED,
        release_id=None,
    )

    request_result = MagicMock()
    request_result.scalar_one_or_none.return_value = request_record
    release_result = MagicMock()
    release_result.scalars.return_value.all.return_value = []
    rules_result = MagicMock()
    rules_result.scalars.return_value.all.return_value = []
    seasons_result = MagicMock()
    seasons_result.scalars.return_value.all.return_value = [season_one]
    episodes_result = MagicMock()
    episodes_result.scalars.return_value.all.return_value = [pending_episode, unreleased_episode]
    mock_db.execute.side_effect = [
        request_result,
        release_result,
        rules_result,
        seasons_result,
        episodes_result,
    ]

    monkeypatch.setattr(dashboard_api, "get_effective_settings", AsyncMock(return_value=MagicMock()))

    class FakeOverseerrService:
        def __init__(self, settings):
            pass

        async def close(self):
            return None

    fake_engine = MagicMock()
    fake_engine.evaluate.return_value = MagicMock(rejection_reason=None, matches=[])
    monkeypatch.setattr(dashboard_api, "OverseerrService", FakeOverseerrService)
    monkeypatch.setattr(
        dashboard_api.RuleEngine,
        "from_db_rules",
        MagicMock(return_value=fake_engine),
    )

    response = await dashboard_api.request_details(
        request_id=51, background_tasks=background_tasks, db=mock_db
    )

    body = json.loads(cast(bytes, response.body))
    assert body["request"]["status"] == RequestStatus.PENDING.value
    assert body["tv_info"]["sync_state"]["needs_plex_enrichment"] is True


@pytest.mark.asyncio
async def test_request_details_surfaces_request_level_tv_aggregate_counts(
    mock_db, monkeypatch, background_tasks
):
    """TV details should surface aggregate episode counts alongside request status."""
    request_record = MagicMock()
    request_record.id = 21
    request_record.media_type = MediaType.TV
    request_record.status = RequestStatus.PARTIALLY_AVAILABLE
    request_record.title = "The Rookie"
    request_record.overseerr_request_id = None

    synced_at = datetime.now(UTC).replace(tzinfo=None)
    season_one = MagicMock(
        id=101,
        season_number=8,
        status=RequestStatus.PARTIALLY_AVAILABLE,
        synced_at=synced_at,
    )
    available_episode = MagicMock(
        id=201,
        season_id=101,
        episode_number=15,
        title="Episode 15",
        air_date=None,
        status=RequestStatus.AVAILABLE,
        release_id=None,
    )
    pending_episode = MagicMock(
        id=202,
        season_id=101,
        episode_number=16,
        title="Episode 16",
        air_date=None,
        status=RequestStatus.PENDING,
        release_id=None,
    )
    unreleased_episode = MagicMock(
        id=203,
        season_id=101,
        episode_number=17,
        title="Episode 17",
        air_date=(datetime.now(UTC) + timedelta(days=7)).date(),
        status=RequestStatus.UNRELEASED,
        release_id=None,
    )

    request_result = MagicMock()
    request_result.scalar_one_or_none.return_value = request_record
    release_result = MagicMock()
    release_result.scalars.return_value.all.return_value = []
    rules_result = MagicMock()
    rules_result.scalars.return_value.all.return_value = []
    seasons_result = MagicMock()
    seasons_result.scalars.return_value.all.return_value = [season_one]
    episodes_result = MagicMock()
    episodes_result.scalars.return_value.all.return_value = [
        available_episode,
        pending_episode,
        unreleased_episode,
    ]
    mock_db.execute.side_effect = [
        request_result,
        release_result,
        rules_result,
        seasons_result,
        episodes_result,
    ]

    monkeypatch.setattr(dashboard_api, "get_effective_settings", AsyncMock(return_value=MagicMock()))

    class FakeOverseerrService:
        def __init__(self, settings):
            pass

        async def close(self):
            return None

    fake_engine = MagicMock()
    fake_engine.evaluate.return_value = MagicMock(rejection_reason=None, matches=[])
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
    assert body["request"]["status"] == RequestStatus.PARTIALLY_AVAILABLE.value
    assert body["tv_info"]["aggregate_counts"] == {
        "available": 1,
        "pending": 1,
        "unreleased": 1,
        "total": 3,
    }
