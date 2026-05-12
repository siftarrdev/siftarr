"""Focused dashboard API tests."""

import json
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.siftarr.models.release import Release
from app.siftarr.models.request import MediaType, RequestStatus
from app.siftarr.routers import dashboard_api
from app.siftarr.services.dashboard import detail_service


@pytest.mark.asyncio
async def test_details_api_normalizes_invalid_release_controls(
    mock_db, monkeypatch, background_tasks
):
    request_record = MagicMock(
        id=21,
        media_type=MediaType.MOVIE,
        status=RequestStatus.PENDING,
        title="Foundation",
        overseerr_request_id=None,
    )
    stored_release = Release(
        id=8,
        request_id=21,
        title="Foundation.2160p.WEB-DL",
        size=30,
        seeders=55,
        leechers=4,
        download_url="https://example.test/foundation",
        indexer="IndexerA",
        resolution="2160p",
        score=95,
        passed_rules=True,
    )
    count_result = MagicMock()
    count_result.scalar.return_value = 1
    release_result = MagicMock()
    release_result.scalars.return_value.all.return_value = [stored_release]
    rules_result = MagicMock()
    rules_result.scalars.return_value.all.return_value = []
    mock_db.execute.side_effect = [count_result, release_result, rules_result]

    monkeypatch.setattr(dashboard_api, "get_settings", lambda: MagicMock())
    monkeypatch.setattr(
        dashboard_api, "load_request_or_404", AsyncMock(return_value=request_record)
    )

    class FakeOverseerrService:
        def __init__(self, settings):
            pass

        async def close(self):
            return None

    fake_engine = MagicMock()
    fake_engine.evaluate.return_value = MagicMock(rejection_reason=None, matches=[])
    monkeypatch.setattr(
        "app.siftarr.services.metadata_service.OverseerrService", FakeOverseerrService
    )
    monkeypatch.setattr(
        detail_service.RuleEngine, "from_db_rules", MagicMock(return_value=fake_engine)
    )

    response = await dashboard_api.request_details(
        request_id=21,
        background_tasks=background_tasks,
        db=mock_db,
        resolution="unsupported",
        sort="unsupported",
        direction="sideways",
    )

    body = json.loads(cast(bytes, response.body))
    assert body["release_controls"]["resolution"] == "all"
    assert body["release_controls"]["sort"] == "score"
    assert body["release_controls"]["direction"] == "desc"
    assert body["filtered_total_releases"] == 1
