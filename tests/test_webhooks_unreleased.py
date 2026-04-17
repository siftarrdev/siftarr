"""Tests for the webhook background task's unreleased-evaluation gate."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.siftarr.models._base import Base
from app.siftarr.models.request import MediaType, Request, RequestStatus
from app.siftarr.routers import webhooks


@pytest_asyncio.fixture
async def session_maker():
    """Provide an in-memory async_sessionmaker with all tables created."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    yield maker
    await engine.dispose()


async def _seed_movie_request(maker, *, status: RequestStatus = RequestStatus.PENDING) -> int:
    async with maker() as db:
        req = Request(
            external_id="ext-1",
            media_type=MediaType.MOVIE,
            tmdb_id=123,
            title="Test Movie",
            status=status,
        )
        db.add(req)
        await db.commit()
        await db.refresh(req)
        return req.id


@pytest.mark.asyncio
async def test_webhook_unreleased_verdict_skips_decision_service(session_maker):
    """When evaluator returns UNRELEASED, the decision service must not run."""
    request_id = await _seed_movie_request(session_maker)

    evaluator_instance = MagicMock()
    evaluator_instance.evaluate_and_apply = AsyncMock(return_value=RequestStatus.UNRELEASED)
    evaluator_cls = MagicMock(return_value=evaluator_instance)

    overseerr_instance = MagicMock()
    overseerr_instance.close = AsyncMock()
    overseerr_cls = MagicMock(return_value=overseerr_instance)

    movie_decision_cls = MagicMock()
    tv_decision_cls = MagicMock()

    with (
        patch.object(webhooks, "async_session_maker", session_maker),
        patch.object(webhooks, "UnreleasedEvaluator", evaluator_cls),
        patch.object(webhooks, "OverseerrService", overseerr_cls),
        patch.object(webhooks, "MovieDecisionService", movie_decision_cls),
        patch.object(webhooks, "TVDecisionService", tv_decision_cls),
        patch.object(webhooks, "ProwlarrService") as prowlarr_cls,
        patch.object(webhooks, "QbittorrentService") as qbit_cls,
    ):
        await webhooks.process_request_background(request_id)

    evaluator_instance.evaluate_and_apply.assert_awaited_once()
    overseerr_instance.close.assert_awaited()
    # Neither decision service nor its dependencies should be instantiated.
    movie_decision_cls.assert_not_called()
    tv_decision_cls.assert_not_called()
    prowlarr_cls.assert_not_called()
    qbit_cls.assert_not_called()


@pytest.mark.asyncio
async def test_webhook_released_verdict_runs_decision_service(session_maker):
    """When evaluator returns no transition, decision service runs normally."""
    request_id = await _seed_movie_request(session_maker)

    evaluator_instance = MagicMock()
    evaluator_instance.evaluate_and_apply = AsyncMock(return_value=None)
    evaluator_cls = MagicMock(return_value=evaluator_instance)

    overseerr_instance = MagicMock()
    overseerr_instance.close = AsyncMock()
    overseerr_cls = MagicMock(return_value=overseerr_instance)

    decision_instance = MagicMock()
    decision_instance.process_request = AsyncMock(return_value={"status": "searching"})
    movie_decision_cls = MagicMock(return_value=decision_instance)

    prowlarr_instance = MagicMock()
    qbit_instance = MagicMock()

    with (
        patch.object(webhooks, "async_session_maker", session_maker),
        patch.object(webhooks, "UnreleasedEvaluator", evaluator_cls),
        patch.object(webhooks, "OverseerrService", overseerr_cls),
        patch.object(webhooks, "MovieDecisionService", movie_decision_cls),
        patch.object(webhooks, "ProwlarrService", MagicMock(return_value=prowlarr_instance)),
        patch.object(webhooks, "QbittorrentService", MagicMock(return_value=qbit_instance)),
    ):
        await webhooks.process_request_background(request_id)

    evaluator_instance.evaluate_and_apply.assert_awaited_once()
    overseerr_instance.close.assert_awaited()
    movie_decision_cls.assert_called_once()
    decision_instance.process_request.assert_awaited_once_with(request_id)


@pytest.mark.asyncio
async def test_webhook_evaluator_exception_does_not_block_processing(session_maker):
    """A raise inside the evaluator must be logged and processing must continue."""
    request_id = await _seed_movie_request(session_maker)

    evaluator_instance = MagicMock()
    evaluator_instance.evaluate_and_apply = AsyncMock(side_effect=RuntimeError("boom"))
    evaluator_cls = MagicMock(return_value=evaluator_instance)

    overseerr_instance = MagicMock()
    overseerr_instance.close = AsyncMock()
    overseerr_cls = MagicMock(return_value=overseerr_instance)

    decision_instance = MagicMock()
    decision_instance.process_request = AsyncMock(return_value={"status": "searching"})
    movie_decision_cls = MagicMock(return_value=decision_instance)

    with (
        patch.object(webhooks, "async_session_maker", session_maker),
        patch.object(webhooks, "UnreleasedEvaluator", evaluator_cls),
        patch.object(webhooks, "OverseerrService", overseerr_cls),
        patch.object(webhooks, "MovieDecisionService", movie_decision_cls),
        patch.object(webhooks, "ProwlarrService", MagicMock()),
        patch.object(webhooks, "QbittorrentService", MagicMock()),
    ):
        await webhooks.process_request_background(request_id)

    evaluator_instance.evaluate_and_apply.assert_awaited_once()
    overseerr_instance.close.assert_awaited()
    # Fail-open: decision service still runs.
    movie_decision_cls.assert_called_once()
    decision_instance.process_request.assert_awaited_once_with(request_id)
