"""Tests for scheduler integration with UnreleasedEvaluator."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.siftarr.models._base import Base
from app.siftarr.models.pending_queue import PendingQueue
from app.siftarr.models.request import MediaType, Request, RequestStatus
from app.siftarr.services.scheduler_service import SchedulerService


@pytest_asyncio.fixture
async def session_factory():
    """Provide an in-memory SQLite async_sessionmaker with schema created."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    yield maker
    await engine.dispose()


@pytest.mark.asyncio
async def test_scheduler_skips_search_when_unreleased(session_factory):
    """When evaluator returns UNRELEASED, the decision service must not run and
    the pending-queue row must be removed."""
    # Seed a request + pending-queue row.
    async with session_factory() as db:
        req = Request(
            external_id="ext-unreleased-1",
            media_type=MediaType.MOVIE,
            tmdb_id=4242,
            title="Future Movie",
            year=2099,
            status=RequestStatus.PENDING,
        )
        db.add(req)
        await db.commit()
        await db.refresh(req)

        queue_item = PendingQueue(
            request_id=req.id,
            next_retry_at=datetime.now(UTC),
        )
        db.add(queue_item)
        await db.commit()
        await db.refresh(queue_item)
        pending_item_id = queue_item.id
        request_id = req.id

    scheduler = SchedulerService(db_session_factory=session_factory)

    # Patch the evaluator class symbol in the scheduler module so the scheduler
    # gets an instance whose evaluate_and_apply returns UNRELEASED.
    evaluator_instance = MagicMock()
    evaluator_instance.evaluate_and_apply = AsyncMock(return_value=RequestStatus.UNRELEASED)
    evaluator_cls = MagicMock(return_value=evaluator_instance)

    # Patch OverseerrService used for evaluator so close() is awaitable and no HTTP occurs.
    overseerr_instance = MagicMock()
    overseerr_instance.close = AsyncMock()
    overseerr_cls = MagicMock(return_value=overseerr_instance)

    # Patch decision services to detect any accidental invocation.
    movie_decision_instance = MagicMock()
    movie_decision_instance.process_request = AsyncMock(
        return_value={"status": "completed", "message": "ok"}
    )
    movie_decision_cls = MagicMock(return_value=movie_decision_instance)
    tv_decision_cls = MagicMock()

    # Fetch the pending-queue row for passing into _process_pending_item.
    async with session_factory() as db:
        item = await db.get(PendingQueue, pending_item_id)
        assert item is not None

        with (
            patch("app.siftarr.services.scheduler_service.UnreleasedEvaluator", evaluator_cls),
            patch("app.siftarr.services.scheduler_service.OverseerrService", overseerr_cls),
            patch(
                "app.siftarr.services.scheduler_service.MovieDecisionService",
                movie_decision_cls,
            ),
            patch("app.siftarr.services.scheduler_service.TVDecisionService", tv_decision_cls),
        ):
            await scheduler._process_pending_item(item)

    # Evaluator was invoked.
    evaluator_instance.evaluate_and_apply.assert_awaited_once()

    # Decision services were NOT constructed or called.
    movie_decision_cls.assert_not_called()
    tv_decision_cls.assert_not_called()
    movie_decision_instance.process_request.assert_not_awaited()

    # Overseerr evaluator instance was closed.
    overseerr_instance.close.assert_awaited()

    # Pending-queue item was removed.
    async with session_factory() as db:
        remaining = await db.get(PendingQueue, pending_item_id)
        assert remaining is None
        # Request itself is still there.
        req_row = await db.get(Request, request_id)
        assert req_row is not None


# ---------------------------------------------------------------------------
# Phase 5: 6-hour re-evaluation job
# ---------------------------------------------------------------------------


def _make_patches(evaluator_side_effect):
    """Build the patch context managers for scheduler module dependencies.

    evaluator_side_effect: a callable (request) -> RequestStatus | None that
    drives what evaluate_and_apply returns (and also applies any side effects
    like actually transitioning status in the DB).
    """
    overseerr_instance = MagicMock()
    overseerr_instance.close = AsyncMock()
    overseerr_cls = MagicMock(return_value=overseerr_instance)

    evaluator_instance = MagicMock()
    evaluator_instance.evaluate_and_apply = AsyncMock(side_effect=evaluator_side_effect)
    evaluator_cls = MagicMock(return_value=evaluator_instance)

    settings_obj = MagicMock()

    return {
        "overseerr_cls": overseerr_cls,
        "overseerr_instance": overseerr_instance,
        "evaluator_cls": evaluator_cls,
        "evaluator_instance": evaluator_instance,
        "settings_obj": settings_obj,
    }


async def _seed_unreleased_request(
    session_factory,
    external_id: str,
    tmdb_id: int,
    title: str,
) -> int:
    async with session_factory() as db:
        req = Request(
            external_id=external_id,
            media_type=MediaType.MOVIE,
            tmdb_id=tmdb_id,
            title=title,
            year=2099,
            status=RequestStatus.UNRELEASED,
        )
        db.add(req)
        await db.commit()
        await db.refresh(req)
        return req.id


@pytest.mark.asyncio
async def test_reevaluate_transitions_released_requests_to_pending(session_factory):
    """One request flips UNRELEASED -> PENDING; one remains UNRELEASED.

    Assert that the flipped one is enqueued and its DB status is PENDING.
    """
    id_flip = await _seed_unreleased_request(session_factory, "ext-flip", 1111, "Now Released")
    id_keep = await _seed_unreleased_request(session_factory, "ext-keep", 2222, "Still Future")

    async def fake_evaluate(req: Request):
        """Simulate the evaluator: actually mutate DB for the flipped one."""
        if req.id == id_flip:
            # Mutate status on the tracked object so commit persists it.
            req.status = RequestStatus.PENDING
            return RequestStatus.PENDING
        return None  # no transition

    patches = _make_patches(fake_evaluate)
    scheduler = SchedulerService(db_session_factory=session_factory)

    with (
        patch(
            "app.siftarr.services.scheduler_service.UnreleasedEvaluator",
            patches["evaluator_cls"],
        ),
        patch(
            "app.siftarr.services.scheduler_service.OverseerrService",
            patches["overseerr_cls"],
        ),
        patch(
            "app.siftarr.services.scheduler_service.get_effective_settings",
            AsyncMock(return_value=patches["settings_obj"]),
        ),
    ):
        count = await scheduler.trigger_reevaluate_unreleased_now()

    assert count == 2
    assert patches["evaluator_instance"].evaluate_and_apply.await_count == 2
    patches["overseerr_instance"].close.assert_awaited()

    # Verify DB: flipped request is PENDING, kept request is UNRELEASED.
    async with session_factory() as db:
        flipped = await db.get(Request, id_flip)
        kept = await db.get(Request, id_keep)
        assert flipped is not None and flipped.status == RequestStatus.PENDING
        assert kept is not None and kept.status == RequestStatus.UNRELEASED

        # Enqueue happened only for the flipped one.
        from sqlalchemy import select as _select

        rows = (await db.execute(_select(PendingQueue))).scalars().all()
        assert len(rows) == 1
        assert rows[0].request_id == id_flip


@pytest.mark.asyncio
async def test_reevaluate_keeps_still_unreleased_as_unreleased(session_factory):
    """When evaluator returns None for everyone, no pending queue rows are added."""
    await _seed_unreleased_request(session_factory, "ext-a", 3333, "Future A")
    await _seed_unreleased_request(session_factory, "ext-b", 4444, "Future B")

    async def fake_evaluate(req: Request):
        return None

    patches = _make_patches(fake_evaluate)
    scheduler = SchedulerService(db_session_factory=session_factory)

    with (
        patch(
            "app.siftarr.services.scheduler_service.UnreleasedEvaluator",
            patches["evaluator_cls"],
        ),
        patch(
            "app.siftarr.services.scheduler_service.OverseerrService",
            patches["overseerr_cls"],
        ),
        patch(
            "app.siftarr.services.scheduler_service.get_effective_settings",
            AsyncMock(return_value=patches["settings_obj"]),
        ),
    ):
        count = await scheduler.trigger_reevaluate_unreleased_now()

    assert count == 2
    async with session_factory() as db:
        from sqlalchemy import select as _select

        rows = (await db.execute(_select(PendingQueue))).scalars().all()
        assert rows == []


@pytest.mark.asyncio
async def test_reevaluate_enqueues_pending_queue_item_on_transition(session_factory):
    """After a transition to PENDING, a PendingQueue row must exist for that request."""
    req_id = await _seed_unreleased_request(session_factory, "ext-trans", 5555, "Transitioned")

    async def fake_evaluate(req: Request):
        req.status = RequestStatus.PENDING
        return RequestStatus.PENDING

    patches = _make_patches(fake_evaluate)
    scheduler = SchedulerService(db_session_factory=session_factory)

    with (
        patch(
            "app.siftarr.services.scheduler_service.UnreleasedEvaluator",
            patches["evaluator_cls"],
        ),
        patch(
            "app.siftarr.services.scheduler_service.OverseerrService",
            patches["overseerr_cls"],
        ),
        patch(
            "app.siftarr.services.scheduler_service.get_effective_settings",
            AsyncMock(return_value=patches["settings_obj"]),
        ),
    ):
        await scheduler.trigger_reevaluate_unreleased_now()

    async with session_factory() as db:
        from sqlalchemy import select as _select

        rows = (
            (await db.execute(_select(PendingQueue).where(PendingQueue.request_id == req_id)))
            .scalars()
            .all()
        )
        assert len(rows) == 1


@pytest.mark.asyncio
async def test_start_registers_reevaluate_job(session_factory):
    """start() must register a job with id=reevaluate_unreleased on a 6h interval."""
    from datetime import timedelta

    scheduler = SchedulerService(db_session_factory=session_factory)
    try:
        scheduler.start()
        assert scheduler.scheduler is not None
        job = scheduler.scheduler.get_job("reevaluate_unreleased")
        assert job is not None
        assert job.name == "Re-evaluate unreleased requests"
        # Trigger is an IntervalTrigger with 6-hour interval.
        assert hasattr(job.trigger, "interval")
        assert job.trigger.interval == timedelta(hours=6)
    finally:
        scheduler.stop()
