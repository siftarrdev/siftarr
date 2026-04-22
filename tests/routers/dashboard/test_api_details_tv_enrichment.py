"""Tests for dashboard TV enrichment metadata."""

import json
from datetime import UTC, datetime, timedelta
from typing import cast
from unittest.mock import MagicMock

import pytest

from app.siftarr.models.request import MediaType, RequestStatus
from app.siftarr.routers import dashboard_api
from app.siftarr.services import dashboard_service, tv_details_service


@pytest.mark.asyncio
async def test_request_details_serializes_unreleased_and_pending_tv_counts(
    mock_db, monkeypatch, background_tasks
):
    """TV details should preserve completed, pending, and unreleased episodes."""
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
        status=RequestStatus.PENDING,
        synced_at=synced_at,
    )
    available_episode = MagicMock(
        id=201,
        season_id=101,
        episode_number=15,
        title="Episode 15",
        air_date=None,
        status=RequestStatus.COMPLETED,
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

    monkeypatch.setattr(dashboard_api, "get_settings", lambda: MagicMock())

    class FakeOverseerrService:
        def __init__(self, settings):
            pass

        async def close(self):
            return None

    fake_engine = MagicMock()
    fake_engine.evaluate.return_value = MagicMock(rejection_reason=None, matches=[])
    monkeypatch.setattr(dashboard_service, "OverseerrService", FakeOverseerrService)
    monkeypatch.setattr(
        dashboard_service.RuleEngine,
        "from_db_rules",
        MagicMock(return_value=fake_engine),
    )

    response = await dashboard_api.request_details(
        request_id=21, background_tasks=background_tasks, db=mock_db
    )

    body = json.loads(cast(bytes, response.body))
    season_payload = body["tv_info"]["seasons"][0]
    assert season_payload["status"] == RequestStatus.PENDING.value
    assert season_payload["available_count"] == 1
    assert season_payload["pending_count"] == 0
    assert season_payload["unreleased_count"] == 1
    assert [episode["status"] for episode in season_payload["episodes"]] == [
        RequestStatus.COMPLETED.value,
        RequestStatus.UNRELEASED.value,
    ]


@pytest.mark.asyncio
async def test_request_details_flags_fresh_pending_tv_data_for_plex_enrichment(
    mock_db, monkeypatch, background_tasks
):
    """Fresh pending seasons with 0 completed episodes should trigger Plex enrichment."""
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

    monkeypatch.setattr(dashboard_api, "get_settings", lambda: MagicMock())

    class FakeOverseerrService:
        def __init__(self, settings):
            pass

        async def close(self):
            return None

    fake_engine = MagicMock()
    fake_engine.evaluate.return_value = MagicMock(rejection_reason=None, matches=[])
    scheduled = []
    monkeypatch.setattr(dashboard_service, "OverseerrService", FakeOverseerrService)
    monkeypatch.setattr(
        dashboard_service.RuleEngine,
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
    assert season_payload["status"] == RequestStatus.PENDING.value
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

    monkeypatch.setattr(dashboard_api, "get_settings", lambda: MagicMock())

    class FakeOverseerrService:
        def __init__(self, settings):
            pass

        async def close(self):
            return None

    fake_engine = MagicMock()
    fake_engine.evaluate.return_value = MagicMock(rejection_reason=None, matches=[])
    monkeypatch.setattr(dashboard_service, "OverseerrService", FakeOverseerrService)
    monkeypatch.setattr(
        dashboard_service.RuleEngine,
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
    available_episode = MagicMock(
        id=201,
        season_id=101,
        episode_number=15,
        title="Episode 15",
        air_date=None,
        status=RequestStatus.COMPLETED,
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

    monkeypatch.setattr(dashboard_api, "get_settings", lambda: MagicMock())

    class FakeOverseerrService:
        def __init__(self, settings):
            pass

        async def close(self):
            return None

    fake_engine = MagicMock()
    fake_engine.evaluate.return_value = MagicMock(rejection_reason=None, matches=[])
    monkeypatch.setattr(dashboard_service, "OverseerrService", FakeOverseerrService)
    monkeypatch.setattr(
        dashboard_service.RuleEngine,
        "from_db_rules",
        MagicMock(return_value=fake_engine),
    )

    response = await dashboard_api.request_details(
        request_id=21, background_tasks=background_tasks, db=mock_db
    )

    body = json.loads(cast(bytes, response.body))
    assert body["request"]["status"] == RequestStatus.PENDING.value
    assert body["tv_info"]["aggregate_counts"] == {
        "available": 1,
        "pending": 1,
        "unreleased": 1,
        "total": 3,
    }
