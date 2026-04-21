"""Tests for dashboard request staging detail payloads."""

import json
from typing import cast
from unittest.mock import MagicMock

import pytest

from app.siftarr.models.release import Release
from app.siftarr.models.request import MediaType, RequestStatus
from app.siftarr.routers import dashboard_api


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
    mock_db.execute.side_effect = [
        request_result,
        release_result,
        rules_result,
        active_stage_result,
    ]

    monkeypatch.setattr(dashboard_api, "get_settings", lambda: MagicMock())

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
    )
    episode_two = MagicMock(
        id=202,
        season_id=101,
        episode_number=2,
        title="Episode 2",
        air_date=None,
        status=RequestStatus.PENDING,
    )

    request_result = MagicMock()
    request_result.scalar_one_or_none.return_value = request_record
    release_result = MagicMock()
    release_result.scalars.return_value.all.return_value = [
        episode_one_release,
        episode_two_release,
    ]
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

    monkeypatch.setattr(dashboard_api, "get_settings", lambda: MagicMock())

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
