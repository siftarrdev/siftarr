"""Search-history dashboard API tests."""

import json
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.siftarr.routers import dashboard_api


@pytest.mark.asyncio
async def test_request_search_history_returns_paginated_payload(mock_db, monkeypatch):
    monkeypatch.setattr(dashboard_api, "load_request_or_404", AsyncMock(return_value=MagicMock()))

    class FakeHistoryService:
        def __init__(self, db):
            assert db is mock_db

        async def list_runs(self, request_id, **kwargs):
            assert request_id == 12
            assert kwargs["limit"] == 5
            assert kwargs["status"] == "completed"
            return {"request_id": request_id, "total": 1, "runs": [{"id": 3}]}

    monkeypatch.setattr(dashboard_api, "SearchHistoryService", FakeHistoryService)

    response = await dashboard_api.request_search_history(
        request_id=12,
        db=mock_db,
        limit=5,
        status="completed",
    )

    body = json.loads(cast(bytes, response.body))
    assert body == {"request_id": 12, "total": 1, "runs": [{"id": 3}]}
