"""Webhook validation at the HTTP boundary, before persistence or processing."""

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import FastAPI

from app.siftarr.database import get_db
from app.siftarr.models import MediaType
from app.siftarr.routers import webhooks


@pytest.fixture
def webhook_app(monkeypatch):
    db = AsyncMock()
    db.add = MagicMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    db.execute.return_value = result

    async def refresh(request):
        request.id = 42

    db.refresh.side_effect = refresh
    metadata = AsyncMock(return_value=("Example", 2026))
    process = AsyncMock()
    monkeypatch.setattr(webhooks, "extract_media_title_and_year", metadata)
    monkeypatch.setattr(webhooks, "process_request_background", process)

    app = FastAPI()
    app.include_router(webhooks.router)
    app.dependency_overrides[get_db] = lambda: db
    return app, db, metadata, process


@pytest.mark.parametrize(
    "media",
    [
        {"media_type": "music", "tmdbid": 123},
        {"media_type": "movie"},
        {"media_type": "tv", "tmdbid": None, "tvdbid": None},
        {"media_type": "movie", "tmdbid": 0},
        {"media_type": "tv", "tvdbid": -1},
        {"media_type": "tv", "tmdbid": 123, "tvdbid": 0},
    ],
)
async def test_invalid_media_returns_422_without_side_effects(webhook_app, media):
    app, db, metadata, process = webhook_app
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/webhook/overseerr", json={"event": "mediarequested", "media": media}
        )

    assert response.status_code == 422
    db.execute.assert_not_awaited()
    db.add.assert_not_called()
    db.commit.assert_not_awaited()
    metadata.assert_not_awaited()
    process.assert_not_awaited()


@pytest.mark.parametrize("event", ["mediarequested", "mediaapproved"])
@pytest.mark.parametrize(
    ("media", "expected_type", "expected_id"),
    [
        ({"media_type": "movie", "tmdbid": 123}, MediaType.MOVIE, "123-7"),
        ({"media_type": "tv", "tvdbid": 456}, MediaType.TV, "456-7"),
        ({"media_type": "tv", "tmdbid": 123, "tvdbid": 456}, MediaType.TV, "123-7"),
    ],
)
async def test_valid_media_creates_request(webhook_app, event, media, expected_type, expected_id):
    app, db, metadata, process = webhook_app
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/webhook/overseerr", json={"event": event, "media": media, "request": {"id": 7}}
        )

    assert response.status_code == 200
    assert response.json() == {"status": "accepted", "request_id": 42}
    request = db.add.call_args.args[0]
    assert request.media_type == expected_type
    assert request.external_id == expected_id
    assert request.title == "Example"
    db.commit.assert_awaited_once()
    metadata.assert_awaited_once()
    process.assert_awaited_once_with(42)
