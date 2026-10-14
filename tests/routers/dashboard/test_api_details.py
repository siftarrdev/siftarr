"""Tests for dashboard request detail payloads."""

import json
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from unittest.mock import MagicMock

import pytest

from app.siftarr.models.request import MediaType, RequestStatus
from app.siftarr.routers import dashboard_api
from app.siftarr.services import metadata_service
from app.siftarr.services.dashboard import tv_details_service
from app.siftarr.services.dashboard.tv_enrichment_service import TVEnrichmentService
from app.siftarr.services.releases.release_serializers import apply_active_selection_metadata
from tests.factories import (
    make_episode,
    make_release,
    make_request,
    make_rule,
    make_season,
    make_staged_torrent,
)

GB = 1024**3
S = RequestStatus


@pytest.fixture(autouse=True)
def _details_env(monkeypatch):
    class FakeOverseerrService:
        def __init__(self, settings):
            pass

        async def close(self):
            return None

    monkeypatch.setattr(dashboard_api, "get_settings", lambda: MagicMock())
    monkeypatch.setattr(metadata_service, "OverseerrService", FakeOverseerrService)


@pytest.fixture
def scheduled_refreshes(monkeypatch) -> list:
    scheduled: list = []
    monkeypatch.setattr(
        tv_details_service,
        "schedule_background_episode_refresh",
        lambda tasks, request_id: scheduled.append((tasks, request_id)) or True,
    )
    return scheduled


async def _details(db, request, background_tasks, **kwargs) -> dict:
    await db.commit()
    response = await dashboard_api.request_details(
        request_id=request.id, background_tasks=background_tasks, db=db, **kwargs
    )
    return json.loads(cast(bytes, response.body))


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


async def test_request_details_orders_releases_and_groups_multi_season_packs_by_season(
    db, background_tasks
):
    """Stored releases sort by score desc then size asc, and a multi-season pack appears
    under every season it covers. Invalid control values fall back to the defaults."""
    show = await make_request(db, media_type=MediaType.TV, title="Foundation")
    await make_season(db, show, 1)
    await make_season(db, show, 2)
    await make_release(
        db, show, title="Foundation.S01-S02.2160p.WEB-DL", size=30 * GB, score=95,
        season_number=1, season_coverage="1,2", resolution="2160p",
    )  # fmt: skip
    await make_release(
        db, show, title="Foundation.S01.720p.WEB-DL", size=10 * GB, score=90,
        season_number=1, season_coverage="1", resolution="720p",
    )  # fmt: skip
    await make_release(
        db, show, title="Foundation.S01-02.1080p.WEB-DL", size=20 * GB, score=95,
        season_number=1, season_coverage="1,2", resolution="1080p",
    )  # fmt: skip

    body = await _details(
        db, show, background_tasks, resolution="bogus", sort="bogus", direction="sideways"
    )

    ordered = [
        "Foundation.S01-02.1080p.WEB-DL",
        "Foundation.S01-S02.2160p.WEB-DL",
        "Foundation.S01.720p.WEB-DL",
    ]
    assert [r["title"] for r in body["releases"]] == ordered
    assert body["release_controls"] == {
        **body["release_controls"],
        "resolution": "all",
        "sort": "score",
        "direction": "desc",
    }
    by_season = body["tv_info"]["releases_by_season"]
    assert [r["title"] for r in by_season["1"]] == ordered
    assert [r["title"] for r in by_season["2"]] == ordered[:2]
    first = body["releases"][0]
    assert first["covered_seasons"] == [1, 2]
    assert first["covers_all_known_seasons"] is True
    assert first["size_per_season"] == "10.00 GB"
    assert all("_size_bytes" not in release for release in body["releases"])
    assert body["filtered_total_releases"] == 3


async def test_request_details_reports_rejection_status_reason_and_publish_date(
    db, background_tasks
):
    await make_rule(db, name="No 2160p", pattern="2160p")
    movie = await make_request(db, title="Foundation")
    published_at = datetime.now(UTC) - timedelta(days=2)
    release = await make_release(
        db, movie, title="Foundation.2160p.WEB-DL", publish_date=published_at, passed_rules=False
    )

    body = await _details(db, movie, background_tasks)

    first = body["releases"][0]
    assert first["stored_release_id"] == release.id
    assert first["status"] == "rejected"
    assert first["status_label"] == "Rejected"
    assert first["rejection_reason"]
    assert first["publish_date"] == published_at.isoformat()


async def test_request_details_marks_the_active_staged_movie_release(db, background_tasks):
    movie = await make_request(db, status=S.STAGED, title="Foundation")
    await make_release(db, movie, title="Foundation.2160p.WEB-DL", score=95)
    await make_release(db, movie, title="Foundation.1080p.WEB-DL", score=90)
    stage = await make_staged_torrent(db, movie, title="Foundation.2160p.WEB-DL")

    body = await _details(db, movie, background_tasks)

    expected = {
        "id": stage.id,
        "title": "Foundation.2160p.WEB-DL",
        "status": "staged",
        "selection_source": "rule",
        "target_scope": {"type": "request"},
    }
    assert body["active_staged_torrent"] == expected
    assert body["active_staged_torrents"] == [expected]
    active, other = body["releases"]
    assert active["is_active_selection"] is True
    assert active["active_selection_source"] == "rule"
    assert active["active_staged_torrent"] == expected
    assert other["is_active_selection"] is False


async def test_request_details_restores_persisted_active_stage_for_tv_episode(db, background_tasks):
    show = await make_request(db, media_type=MediaType.TV, title="Foundation")
    season = await make_season(db, show, 1)
    await make_episode(db, season, 1)
    await make_episode(db, season, 2)
    await make_release(
        db, show, title="Foundation.S01E01.1080p.WEB-DL", season_number=1, episode_number=1,
        score=95,
    )  # fmt: skip
    await make_release(
        db, show, title="Foundation.S01E02.1080p.WEB-DL", season_number=1, episode_number=2,
        score=90,
    )  # fmt: skip
    stage = await make_staged_torrent(
        db, show, title="Foundation.S01E01.1080p.WEB-DL", selection_source="manual"
    )

    body = await _details(db, show, background_tasks)

    scope = {"type": "single_episode", "season_number": 1, "episode_number": 1}
    expected = {
        "id": stage.id,
        "title": "Foundation.S01E01.1080p.WEB-DL",
        "status": "staged",
        "selection_source": "manual",
        "target_scope": scope,
    }
    assert body["active_staged_torrents"] == [expected]
    first, second = body["releases"]
    assert first["target_scope"] == scope
    assert first["is_active_selection"] is True
    assert first["active_staged_torrent"] == expected
    assert second["target_scope"] == {**scope, "episode_number": 2}
    assert second["is_active_selection"] is False
    assert second["active_staged_torrent"] is None


async def test_request_details_serializes_tv_episode_counts(db, background_tasks):
    """Season and request-level counts keep completed, pending and unreleased episodes apart."""
    show = await make_request(db, media_type=MediaType.TV, title="The Rookie")
    season = await make_season(db, show, 8)
    future = (datetime.now(UTC) + timedelta(days=7)).date()
    await make_episode(db, season, 15, status=S.COMPLETED)
    await make_episode(db, season, 16, status=S.PENDING)
    await make_episode(db, season, 17, status=S.UNRELEASED, air_date=future)

    body = await _details(db, show, background_tasks)

    season_payload = body["tv_info"]["seasons"][0]
    assert season_payload["available_count"] == 1
    assert season_payload["pending_count"] == 1
    assert season_payload["unreleased_count"] == 1
    assert season_payload["total_count"] == 3
    assert [e["status"] for e in season_payload["episodes"]] == [
        S.COMPLETED.value,
        S.PENDING.value,
        S.UNRELEASED.value,
    ]
    assert season_payload["episodes"][2]["air_date"] == future.isoformat()
    assert body["tv_info"]["aggregate_counts"] == {
        "available": 1,
        "pending": 1,
        "unreleased": 1,
        "total": 3,
    }


@pytest.mark.parametrize(
    ("synced_days_ago", "episode_statuses", "stale"),
    [
        (2, [S.PENDING], True),
        (0, [S.PENDING, S.UNRELEASED], False),
    ],
    ids=["stale-cache", "fresh-pending-and-unreleased"],
)
async def test_request_details_returns_cached_tv_data_and_schedules_refresh(
    db, background_tasks, scheduled_refreshes, synced_days_ago, episode_statuses, stale
):
    """TV details return persisted seasons immediately and refresh in the background."""
    show = await make_request(db, media_type=MediaType.TV, title="Foundation")
    season = await make_season(db, show, 1)
    season.synced_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=synced_days_ago)
    for number, status in enumerate(episode_statuses, start=1):
        await make_episode(db, season, number, status=status, title=f"Episode {number}")

    body = await _details(db, show, background_tasks)

    assert body["tv_info"]["seasons"][0]["episodes"][0]["title"] == "Episode 1"
    sync_state = body["tv_info"]["sync_state"]
    assert sync_state["stale"] is stale
    assert sync_state["needs_plex_enrichment"] is True
    assert sync_state["refresh_in_progress"] is True
    assert scheduled_refreshes == [(background_tasks, show.id)]
