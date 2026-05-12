from collections.abc import AsyncGenerator
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.siftarr.config import reload_settings
from app.siftarr.database import get_db
from app.siftarr.main import create_app
from app.siftarr.models import Base, Request, StatsReleaseFact
from app.siftarr.models.request import MediaType, RequestStatus
from app.siftarr.routers import stats


@pytest.fixture
async def session_maker():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


@pytest.fixture(autouse=True)
def _reset_settings_cache():
    yield
    reload_settings()


@pytest.fixture
async def seeded_session_maker(session_maker):
    async with session_maker() as session:
        request = Request(
            external_id="1",
            media_type=MediaType.MOVIE,
            title="Movie",
            status=RequestStatus.COMPLETED,
            created_at=datetime(2026, 5, 1),
        )
        session.add(request)
        await session.flush()
        session.add(
            StatsReleaseFact(
                request_id=request.id,
                title="Movie",
                indexer="IndexerA",
                resolution="1080p",
                resolution_bucket="1080p",
                selection_source="manual",
                approved_at=datetime(2026, 5, 1),
            )
        )
        await session.commit()
    return session_maker


def _client_with_db(session_maker) -> TestClient:
    app = create_app()

    async def override_db() -> AsyncGenerator:
        async with session_maker() as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[stats.get_db] = override_db
    return TestClient(app, raise_server_exceptions=False)


def test_stats_page_is_protected(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    reload_settings()
    client = TestClient(create_app(), raise_server_exceptions=False)

    response = client.get("/stats", headers={"accept": "text/html"}, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"].startswith("/auth/login?next=%2Fstats")


@pytest.mark.asyncio
async def test_stats_page_renders_with_api_key(seeded_session_maker, monkeypatch):
    monkeypatch.setenv("SIFTARR_API_KEY", "test-key")
    reload_settings()
    client = _client_with_db(seeded_session_maker)

    response = client.get("/stats", headers={"X-API-Key": "test-key"})

    assert response.status_code == 200
    assert "Stats" in response.text
    assert "/static/js/stats.js" in response.text


@pytest.mark.asyncio
async def test_stats_json_endpoint_returns_payload(seeded_session_maker, monkeypatch):
    monkeypatch.setenv("SIFTARR_API_KEY", "test-key")
    reload_settings()
    client = _client_with_db(seeded_session_maker)

    response = client.get("/stats/data?range=all", headers={"X-API-Key": "test-key"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["cards"]["total_requests"] == 1
    assert payload["charts"]["source_split"] == [{"label": "IndexerA", "value": 1}]


@pytest.mark.asyncio
async def test_stats_json_endpoint_validates_ranges(seeded_session_maker, monkeypatch):
    monkeypatch.setenv("SIFTARR_API_KEY", "test-key")
    reload_settings()
    client = _client_with_db(seeded_session_maker)

    response = client.get(
        "/stats/data?range=custom&start=2026-05-10&end=2026-05-01",
        headers={"X-API-Key": "test-key"},
    )

    assert response.status_code == 422
    assert "start must be before" in response.json()["detail"]
