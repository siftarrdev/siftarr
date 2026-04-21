"""Tests for dashboard TV sync-state detail endpoints."""

import json
from datetime import UTC, datetime, timedelta
from typing import cast
from unittest.mock import MagicMock

import pytest

from app.siftarr.models.request import MediaType, RequestStatus
from app.siftarr.routers import dashboard_api
from app.siftarr.services import tv_details_service


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
    season_one = MagicMock(
        id=101, season_number=1, status=RequestStatus.PENDING, synced_at=stale_synced_at
    )
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

    monkeypatch.setattr(dashboard_api, "get_settings", lambda: MagicMock())

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
    season_one = MagicMock(
        id=101, season_number=1, status=RequestStatus.COMPLETED, synced_at=synced_at
    )
    episode_one = MagicMock(
        id=201,
        season_id=101,
        episode_number=1,
        title="Pilot",
        air_date=None,
        status=RequestStatus.COMPLETED,
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
    assert body["seasons"][0]["episodes"][0]["status"] == RequestStatus.COMPLETED.value
    assert body["sync_state"]["stale"] is False
    assert body["sync_state"]["refresh_in_progress"] is False
    assert scheduled == []
