"""Tests for timeline data in the request details API endpoint."""

import json
from datetime import UTC, datetime
from typing import cast
from unittest.mock import MagicMock

import pytest
from fastapi import BackgroundTasks

from app.siftarr.models.activity_log import ActivityLog
from app.siftarr.routers import dashboard_api
from tests.factories import make_request


@pytest.mark.parametrize("with_logs", [True, False])
async def test_request_details_timeline_is_chronological_or_empty(db, monkeypatch, with_logs):
    monkeypatch.setattr(dashboard_api, "get_settings", lambda: MagicMock())
    request = await make_request(db)
    if with_logs:
        # Insert newest first: the endpoint must still return oldest first.
        for event_type, hour in (("search_completed", 11), ("search_started", 10)):
            db.add(
                ActivityLog(
                    request_id=request.id,
                    event_type=event_type,
                    created_at=datetime(2026, 4, 20, hour, tzinfo=UTC).replace(tzinfo=None),
                )
            )
    await db.commit()

    response = await dashboard_api.request_details(
        request_id=request.id, background_tasks=BackgroundTasks(), db=db
    )

    timeline = json.loads(cast(bytes, response.body))["timeline"]
    assert [entry["event_type"] for entry in timeline] == (
        ["search_started", "search_completed"] if with_logs else []
    )
