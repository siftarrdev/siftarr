"""Tests for persisted Plex scan state helpers."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.siftarr.models._base import Base
from app.siftarr.models.plex_scan_state import PlexScanState
from app.siftarr.services.plex_scan_state_service import PlexScanStateService


def _naive_utc(value: datetime) -> datetime:
    """Convert aware UTC datetimes to the SQLite round-trip form used in tests."""
    return value.astimezone(UTC).replace(tzinfo=None)


@pytest.mark.asyncio
async def test_acquire_lock_creates_and_releases_scan_state_row():
    """Lock acquisition should create persisted state for a new job."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_maker = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    now = datetime(2026, 4, 19, 12, 0, tzinfo=UTC)
    checkpoint = now - timedelta(minutes=5)
    metrics = {"scanned": 10, "matched": 3}

    async with session_maker() as session:
        service = PlexScanStateService(session)

        state = await service.acquire_lock(
            "plex_recent_scan",
            "worker-a",
            timedelta(minutes=2),
            now=now,
        )

        assert state is not None
        assert state.job_name == "plex_recent_scan"
        assert state.lock_owner == "worker-a"
        assert state.last_started_at == _naive_utc(now)
        assert state.lock_expires_at == _naive_utc(now + timedelta(minutes=2))

        released = await service.release_lock(
            "plex_recent_scan",
            "worker-a",
            success=True,
            finished_at=now + timedelta(minutes=1),
            checkpoint_at=checkpoint,
            metrics_payload=metrics,
        )

        assert released is not None
        assert released.lock_owner is None
        assert released.last_finished_at == _naive_utc(now + timedelta(minutes=1))
        assert released.last_success_at == _naive_utc(now + timedelta(minutes=1))
        assert released.checkpoint_at == _naive_utc(checkpoint)
        assert released.metrics_payload == metrics
        assert released.last_error is None

    await engine.dispose()


@pytest.mark.asyncio
async def test_acquire_lock_denies_overlap_and_recovers_stale_lock():
    """Active leases should block overlap until a stale lock is recovered."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_maker = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    first_now = datetime(2026, 4, 19, 12, 0, tzinfo=UTC)
    stale_now = first_now + timedelta(minutes=10)

    async with session_maker() as session:
        service = PlexScanStateService(session)

        state = await service.acquire_lock(
            "plex_full_reconcile",
            "worker-a",
            timedelta(minutes=2),
            now=first_now,
        )
        assert state is not None

        overlapping = await service.acquire_lock(
            "plex_full_reconcile",
            "worker-b",
            timedelta(minutes=2),
            now=first_now + timedelta(minutes=1),
        )
        assert overlapping is None

        recovered = await service.recover_stale_lock("plex_full_reconcile", now=stale_now)
        assert recovered is not None
        assert recovered.lock_owner is None

        reacquired = await service.acquire_lock(
            "plex_full_reconcile",
            "worker-b",
            timedelta(minutes=3),
            now=stale_now,
        )
        assert reacquired is not None
        assert reacquired.lock_owner == "worker-b"
        assert reacquired.lock_expires_at == _naive_utc(stale_now + timedelta(minutes=3))

    await engine.dispose()


@pytest.mark.asyncio
async def test_update_progress_requires_current_lock_owner_and_persists_atomically():
    """Progress updates should only apply for the active lock owner."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_maker = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    now = datetime(2026, 4, 19, 12, 0, tzinfo=UTC)
    checkpoint = now - timedelta(minutes=3)
    metrics = {"scanned": 5, "deduped": 1}

    async with session_maker() as session:
        service = PlexScanStateService(session)
        await service.acquire_lock("plex_recent_scan", "worker-a", timedelta(minutes=2), now=now)

        rejected = await service.update_progress(
            "plex_recent_scan",
            "worker-b",
            checkpoint_at=checkpoint,
            metrics_payload=metrics,
            last_error="wrong owner",
            now=now,
        )
        assert rejected is None

        state_after_reject = await session.get(PlexScanState, "plex_recent_scan")
        assert state_after_reject is not None
        assert state_after_reject.checkpoint_at is None
        assert state_after_reject.metrics_payload is None
        assert state_after_reject.last_error is None

        updated = await service.update_progress(
            "plex_recent_scan",
            "worker-a",
            checkpoint_at=checkpoint,
            metrics_payload=metrics,
            last_error="temporary plex timeout",
            lease_duration=timedelta(minutes=4),
            now=now,
        )
        assert updated is not None
        assert updated.checkpoint_at == _naive_utc(checkpoint)
        assert updated.metrics_payload == metrics
        assert updated.last_error == "temporary plex timeout"
        assert updated.lock_expires_at == _naive_utc(now + timedelta(minutes=4))

    await engine.dispose()


@pytest.mark.asyncio
async def test_release_lock_preserves_error_for_failed_runs():
    """Failed runs should record completion time without marking success."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_maker = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    started = datetime(2026, 4, 19, 12, 0, tzinfo=UTC)
    finished = started + timedelta(minutes=1)
    metrics = {"scanned": 7, "skipped": 2}

    async with session_maker() as session:
        service = PlexScanStateService(session)
        await service.acquire_lock(
            "plex_recent_scan", "worker-a", timedelta(minutes=2), now=started
        )

        released = await service.release_lock(
            "plex_recent_scan",
            "worker-a",
            success=False,
            finished_at=finished,
            metrics_payload=metrics,
            last_error="plex unavailable",
        )

        assert released is not None
        assert released.lock_owner is None
        assert released.last_finished_at == _naive_utc(finished)
        assert released.last_success_at is None
        assert released.metrics_payload == metrics
        assert released.last_error == "plex unavailable"

    await engine.dispose()
