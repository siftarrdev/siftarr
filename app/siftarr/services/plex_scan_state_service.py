"""Helpers for persisted Plex scan lock and checkpoint state."""

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import cast

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.siftarr.models.plex_scan_state import PlexScanState

_UNSET = object()


def _utc_now() -> datetime:
    """Return current UTC time as timezone-aware datetime."""
    return datetime.now(UTC)


def _normalize_datetime(value: datetime | None) -> datetime | None:
    """Store datetimes in SQLite-friendly naive UTC form."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


def _result_rowcount(result: object) -> int:
    """Safely read an UPDATE result rowcount."""
    rowcount = getattr(result, "rowcount", None)
    return rowcount if isinstance(rowcount, int) else 0


class PlexScanStateService:
    """Manages persisted scan locks, checkpoints, and compact metrics."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get_state(self, job_name: str) -> PlexScanState | None:
        """Return the persisted state for a scan job."""
        return await self.db.get(PlexScanState, job_name, populate_existing=True)

    async def acquire_lock(
        self,
        job_name: str,
        lock_owner: str,
        lease_duration: timedelta,
        *,
        now: datetime | None = None,
    ) -> PlexScanState | None:
        """Acquire the per-job lock when it is free or stale."""
        current_time = _normalize_datetime(now or _utc_now())
        if current_time is None:
            return None
        expires_at = current_time + lease_duration

        values = {
            "lock_owner": lock_owner,
            "lock_acquired_at": current_time,
            "lock_expires_at": expires_at,
            "last_started_at": current_time,
        }

        if await self._update_lock_if_available(job_name, current_time, values):
            return await self.get_state(job_name)

        existing_state = await self.get_state(job_name)
        if existing_state is not None:
            return None

        state = PlexScanState(job_name=job_name, **values)
        self.db.add(state)
        try:
            await self.db.commit()
        except IntegrityError:
            await self.db.rollback()
        else:
            return await self.get_state(job_name)

        if await self._update_lock_if_available(job_name, current_time, values):
            return await self.get_state(job_name)

        return None

    async def recover_stale_lock(
        self,
        job_name: str,
        *,
        now: datetime | None = None,
    ) -> PlexScanState | None:
        """Clear an expired job lock so it can be acquired again."""
        current_time = _normalize_datetime(now or _utc_now())
        if current_time is None:
            return None
        result = await self.db.execute(
            update(PlexScanState)
            .execution_options(synchronize_session=False)
            .where(PlexScanState.job_name == job_name)
            .where(PlexScanState.lock_owner.is_not(None))
            .where(PlexScanState.lock_expires_at.is_not(None))
            .where(PlexScanState.lock_expires_at <= current_time)
            .values(
                lock_owner=None,
                lock_acquired_at=None,
                lock_expires_at=None,
            )
        )
        if _result_rowcount(result) == 0:
            return None

        await self.db.commit()
        return await self.get_state(job_name)

    async def update_progress(
        self,
        job_name: str,
        lock_owner: str,
        *,
        checkpoint_at: datetime | None | object = _UNSET,
        metrics_payload: dict[str, object] | None | object = _UNSET,
        last_error: str | None | object = _UNSET,
        lease_duration: timedelta | None = None,
        now: datetime | None = None,
    ) -> PlexScanState | None:
        """Persist checkpoint, metrics, and optional lease renewal atomically."""
        current_time = _normalize_datetime(now or _utc_now())
        if current_time is None:
            return None
        values: dict[str, object] = {}

        if checkpoint_at is not _UNSET:
            values["checkpoint_at"] = _normalize_datetime(cast("datetime | None", checkpoint_at))
        if metrics_payload is not _UNSET:
            values["metrics_payload"] = metrics_payload
        if last_error is not _UNSET:
            values["last_error"] = last_error
        if lease_duration is not None:
            values["lock_expires_at"] = current_time + lease_duration

        if not values:
            return await self.get_state(job_name)

        result = await self.db.execute(
            update(PlexScanState)
            .execution_options(synchronize_session=False)
            .where(PlexScanState.job_name == job_name)
            .where(PlexScanState.lock_owner == lock_owner)
            .values(**values)
        )
        if _result_rowcount(result) == 0:
            await self.db.rollback()
            return None

        await self.db.commit()
        return await self.get_state(job_name)

    async def release_lock(
        self,
        job_name: str,
        lock_owner: str,
        *,
        success: bool,
        finished_at: datetime | None = None,
        checkpoint_at: datetime | None | object = _UNSET,
        metrics_payload: dict[str, object] | None | object = _UNSET,
        last_error: str | None | object = _UNSET,
    ) -> PlexScanState | None:
        """Release a job lock and persist final checkpoint and metrics state."""
        completed_at = _normalize_datetime(finished_at or _utc_now())
        if completed_at is None:
            return None
        values: dict[str, object] = {
            "lock_owner": None,
            "lock_acquired_at": None,
            "lock_expires_at": None,
            "last_finished_at": completed_at,
        }

        if success:
            values["last_success_at"] = completed_at
            if last_error is _UNSET:
                values["last_error"] = None
        if checkpoint_at is not _UNSET:
            values["checkpoint_at"] = _normalize_datetime(cast("datetime | None", checkpoint_at))
        if metrics_payload is not _UNSET:
            values["metrics_payload"] = metrics_payload
        if last_error is not _UNSET:
            values["last_error"] = last_error

        result = await self.db.execute(
            update(PlexScanState)
            .execution_options(synchronize_session=False)
            .where(PlexScanState.job_name == job_name)
            .where(PlexScanState.lock_owner == lock_owner)
            .values(**values)
        )
        if _result_rowcount(result) == 0:
            await self.db.rollback()
            return None

        await self.db.commit()
        return await self.get_state(job_name)

    async def _update_lock_if_available(
        self,
        job_name: str,
        current_time: datetime,
        values: Mapping[str, object],
    ) -> bool:
        """Acquire an existing lock record when it is unlocked or stale."""
        result = await self.db.execute(
            update(PlexScanState)
            .execution_options(synchronize_session=False)
            .where(PlexScanState.job_name == job_name)
            .where(
                (PlexScanState.lock_owner.is_(None))
                | (PlexScanState.lock_expires_at.is_(None))
                | (PlexScanState.lock_expires_at <= current_time)
            )
            .values(**dict(values))
        )
        if _result_rowcount(result) == 0:
            return False

        await self.db.commit()
        return True
