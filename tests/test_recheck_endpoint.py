"""Tests for the manual recheck-release endpoint (Phase 8)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.siftarr.models._base import Base
from app.siftarr.models.pending_queue import PendingQueue
from app.siftarr.models.request import MediaType, Request, RequestStatus
from app.siftarr.routers import dashboard_actions


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    async with session_maker() as db:
        yield db
    await engine.dispose()


@pytest.fixture(autouse=True)
def _patch_overseerr_and_settings(monkeypatch):
    """Replace heavy collaborators inside the dashboard_actions module."""

    async def fake_get_effective_settings(_db):
        return MagicMock()

    fake_overseerr_instance = MagicMock()
    fake_overseerr_instance.close = AsyncMock()

    def fake_overseerr_ctor(settings):  # noqa: ARG001
        return fake_overseerr_instance

    monkeypatch.setattr(dashboard_actions, "get_effective_settings", fake_get_effective_settings)
    monkeypatch.setattr(dashboard_actions, "OverseerrService", fake_overseerr_ctor)
    return fake_overseerr_instance


async def _make_request(
    db,
    *,
    status: RequestStatus = RequestStatus.UNRELEASED,
    tmdb_id: int | None = 42,
) -> Request:
    req = Request(
        external_id="ext-recheck",
        media_type=MediaType.MOVIE,
        tmdb_id=tmdb_id,
        title="Recheck Movie",
        status=status,
    )
    db.add(req)
    await db.commit()
    await db.refresh(req)
    return req


@pytest.mark.asyncio
async def test_recheck_unreleased_still_unreleased_redirects_back(session, monkeypatch):
    """Evaluator returns None (no transition) → redirect to /?tab=unreleased."""
    req = await _make_request(session)

    fake_evaluator = MagicMock()
    fake_evaluator.evaluate_and_apply = AsyncMock(return_value=None)
    monkeypatch.setattr(dashboard_actions, "UnreleasedEvaluator", lambda db, ov: fake_evaluator)

    response = await dashboard_actions.recheck_release(request_id=req.id, db=session)

    assert response.status_code == 303
    assert response.headers["location"] == "/?tab=unreleased"
    fake_evaluator.evaluate_and_apply.assert_awaited_once()

    # No queue row was created.
    result = await session.execute(select(PendingQueue))
    assert result.scalars().first() is None


@pytest.mark.asyncio
async def test_recheck_transitions_to_pending_and_enqueues(session, monkeypatch):
    """Evaluator flips to PENDING → redirect to /?tab=pending and enqueue."""
    req = await _make_request(session)

    fake_evaluator = MagicMock()
    fake_evaluator.evaluate_and_apply = AsyncMock(return_value=RequestStatus.PENDING)
    monkeypatch.setattr(dashboard_actions, "UnreleasedEvaluator", lambda db, ov: fake_evaluator)

    response = await dashboard_actions.recheck_release(request_id=req.id, db=session)

    assert response.status_code == 303
    assert response.headers["location"] == "/?tab=pending"

    result = await session.execute(select(PendingQueue).where(PendingQueue.request_id == req.id))
    entry = result.scalar_one_or_none()
    assert entry is not None
    assert entry.request_id == req.id


@pytest.mark.asyncio
async def test_recheck_404_on_unknown_request(session):
    """A nonexistent request_id should raise HTTPException(404)."""
    with pytest.raises(HTTPException) as exc_info:
        await dashboard_actions.recheck_release(request_id=9999, db=session)

    assert exc_info.value.status_code == 404
