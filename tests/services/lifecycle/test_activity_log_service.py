"""Tests for ActivityLogService."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select

from app.siftarr.models import ActivityLog, EventType, StatsTimingEvent
from app.siftarr.services.lifecycle.activity_log_service import ActivityLogService
from tests.factories import make_request


@pytest.mark.asyncio
async def test_log_persists_activity_and_timing_rows(db):
    request = await make_request(db)

    result = await ActivityLogService(db).log(
        EventType.SEARCH_STARTED, request_id=request.id, details={"query": "test"}
    )

    assert result is not None
    logs = (await db.execute(select(ActivityLog))).scalars().all()
    assert [(log.event_type, log.request_id) for log in logs] == [("search_started", request.id)]
    assert json.loads(logs[0].details) == {"query": "test"}
    timings = (await db.execute(select(StatsTimingEvent))).scalars().all()
    assert [(t.event_name, t.request_id) for t in timings] == [("search_started", request.id)]


@pytest.mark.asyncio
async def test_log_allows_system_event_without_request_or_details(db):
    result = await ActivityLogService(db).log(EventType.ERROR)

    assert result is not None
    assert result.request_id is None
    assert result.details is None


@pytest.mark.asyncio
async def test_timeline_filters_by_request(db):
    first = await make_request(db, external_id="1")
    second = await make_request(db, external_id="2")
    service = ActivityLogService(db)
    await service.log(EventType.SEARCH_STARTED, request_id=first.id)
    await service.log(EventType.DOWNLOAD_COMPLETED, request_id=second.id)

    timeline = await service.get_timeline(first.id, limit=10)

    assert [log.event_type for log in timeline] == ["search_started"]


@pytest.mark.asyncio
async def test_log_swallows_flush_exception():
    """A failing activity log must not break the caller's workflow."""
    db = AsyncMock()
    db.add = MagicMock()
    nested_context = AsyncMock()
    db.begin_nested = MagicMock(return_value=nested_context)
    db.flush.side_effect = RuntimeError("db down")

    with patch("app.siftarr.services.lifecycle.activity_log_service.logger.exception"):
        result = await ActivityLogService(db).log(EventType.SEARCH_STARTED, request_id=99)

    assert result is None
