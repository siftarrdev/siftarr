"""Tests for dashboard request staging detail payloads."""

import json
from typing import Any, cast
from unittest.mock import MagicMock

import pytest

from app.siftarr.models.release import Release
from app.siftarr.models.request import MediaType, RequestStatus
from app.siftarr.routers import dashboard_api
from app.siftarr.services.dashboard import detail_service
from app.siftarr.services.metadata_service
from app.siftarr.services.releases.release_serializers import apply_active_selection_metadata
from app.siftarr.services.dashboard.tv_enrichment_service import TVEnrichmentService


def test_tv_staged_scope_helpers_cover_episode_season_multi_and_complete():
    service = TVEnrichmentService(db=cast(Any, object()))
    staged_payloads: list[dict[str, object]] = [
        {"target_scope": {"type": "single_episode", "season_number": 3, "episode_number": 3}},
        {"target_scope": {"type": "season_pack", "season_numbers": [4]}},
        {"target_scope": {"type": "multi_season_pack", "season_numbers": [5, 6]}},
        {"target_scope": {"type": "complete_series"}},
    ]

    assert service._episode_has_staged_scope(3, 3, staged_payloads)
    assert service._season_has_staged_scope(4, staged_payloads)
    assert service._episode_has_staged_scope(5, 1, staged_payloads)
    assert service._season_has_staged_scope(99, staged_payloads)


def test_tv_active_selection_matches_title_scope_and_magnet_after_release_refresh():
    releases: list[dict[str, object]] = [
        {
            "title": "Foundation.S01E01.1080p.WEB-DL",
            "magnet_url": "magnet:?xt=urn:btih:s01e01",
            "target_scope": {"type": "single_episode", "season_number": 1, "episode_number": 1},
        },
        {
            "title": "Foundation.S01E02.1080p.WEB-DL",
            "magnet_url": "magnet:?xt=urn:btih:s01e02",
            "target_scope": {"type": "single_episode", "season_number": 1, "episode_number": 2},
        },
    ]
    active_stages: list[dict[str, object]] = [
        {
            "id": 77,
            "title": "Foundation.S01E01.1080p.WEB-DL",
            "status": "staged",
            "selection_source": "manual",
            "magnet_url": "magnet:?xt=urn:btih:s01e01",
            "target_scope": {"type": "single_episode", "season_number": 1, "episode_number": 1},
        }
    ]

    marked = apply_active_selection_metadata(releases, active_stages, media_type=MediaType.TV)

    assert marked[0]["is_active_selection"] is True
    assert marked[0]["conflicts_active_selection"] is True
    assert marked[0]["active_selection_source"] == "manual"
    assert marked[1]["is_active_selection"] is False
    assert marked[1]["conflicts_active_selection"] is False


def test_tv_active_selection_metadata_marks_only_overlapping_replace_scopes():
    releases: list[dict[str, object]] = [
        {
            "title": "Show.S01E01.REPACK.1080p.WEB-DL",
            "target_scope": {"type": "single_episode", "season_number": 1, "episode_number": 1},
        },
        {
            "title": "Show.S01E02.1080p.WEB-DL",
            "target_scope": {"type": "single_episode", "season_number": 1, "episode_number": 2},
        },
        {
            "title": "Show.S01.REPACK.1080p.WEB-DL",
            "target_scope": {"type": "season_pack", "season_numbers": [1]},
        },
        {
            "title": "Show.S02.1080p.WEB-DL",
            "target_scope": {"type": "season_pack", "season_numbers": [2]},
        },
        {
            "title": "Show.S02-S03.1080p.WEB-DL",
            "target_scope": {"type": "multi_season_pack", "season_numbers": [2, 3]},
        },
    ]
    active_stages: list[dict[str, object]] = [
        {
            "id": 77,
            "title": "Show.S01E01.1080p.WEB-DL",
            "status": "staged",
            "selection_source": "manual",
            "target_scope": {"type": "single_episode", "season_number": 1, "episode_number": 1},
        },
        {
            "id": 78,
            "title": "Show.S03.1080p.WEB-DL",
            "status": "staged",
            "selection_source": "manual",
            "target_scope": {"type": "season_pack", "season_numbers": [3]},
        },
    ]

    marked = apply_active_selection_metadata(releases, active_stages, media_type=MediaType.TV)

    assert [release["is_active_selection"] for release in marked] == [
        False,
        False,
        False,
        False,
        False,
    ]
    assert [release["conflicts_active_selection"] for release in marked] == [
        True,
        False,
        True,
        False,
        True,
    ]


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
    count_result = MagicMock()
    count_result.scalar.return_value = 2
    release_result = MagicMock()
    release_result.scalars.return_value.all.return_value = [active_release, other_release]
    rules_result = MagicMock()
    rules_result.scalars.return_value.all.return_value = []
    active_stage_result = MagicMock()
    active_stage_result.scalars.return_value.all.return_value = [active_stage]
    mock_db.execute.side_effect = [
        request_result,
        count_result,
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

    monkeypatch.setattr(metadata_service, "OverseerrService", FakeOverseerrService)
    monkeypatch.setattr(
        detail_service.RuleEngine,
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
async def test_request_details_tv_loads_persisted_active_stage_for_pending_request(
    mock_db, monkeypatch, background_tasks
):
    """TV details should restore staged metadata from persisted rows on initial load."""
    request_record = MagicMock()
    request_record.id = 21
    request_record.media_type = MediaType.TV
    request_record.status = RequestStatus.PENDING
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
    count_result = MagicMock()
    count_result.scalar.return_value = 2
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
        count_result,
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

    monkeypatch.setattr(metadata_service, "OverseerrService", FakeOverseerrService)
    monkeypatch.setattr(
        detail_service.RuleEngine,
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
