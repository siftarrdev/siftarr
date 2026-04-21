"""Tests for Plex scheduler jobs and manual triggers."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.siftarr.models._base import Base
from app.siftarr.models.request import MediaType, Request, RequestStatus
from app.siftarr.services import scheduler_service
from app.siftarr.services.scheduler_service import (
    PLEX_POLL_JOB_NAME,
    PLEX_RECENT_SCAN_JOB_NAME,
    SchedulerService,
)


class _FakeSessionContext:
    def __init__(self, db):
        self.db = db

    async def __aenter__(self):
        return self.db

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeScheduler:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.jobs: list[dict] = []
        self.started = False
        self.stopped = False

    def add_job(self, func, trigger=None, **kwargs):
        self.jobs.append({"func": func, "trigger": trigger, **kwargs})

    def start(self):
        self.started = True

    def shutdown(self):
        self.stopped = True


@pytest.mark.asyncio
async def test_start_registers_recent_scan_and_poll_jobs(monkeypatch):
    """Scheduler startup should register separate recent-scan and poll jobs."""
    created = {}

    def fake_scheduler(**kwargs):
        created["scheduler"] = _FakeScheduler(**kwargs)
        return created["scheduler"]

    monkeypatch.setattr(scheduler_service, "AsyncIOScheduler", fake_scheduler)
    monkeypatch.setattr(
        "app.siftarr.config.get_settings",
        lambda: SimpleNamespace(
            plex_poll_interval_minutes=360,
            plex_recent_scan_interval_minutes=5,
        ),
    )

    service = SchedulerService(lambda: _FakeSessionContext(AsyncMock()), logger=MagicMock())
    service.start()

    fake = created["scheduler"]
    job_ids = {job["id"] for job in fake.jobs}
    assert "plex_recent_scan" in job_ids
    assert "plex_poll" in job_ids
    assert "check_download_completion" in job_ids
    assert "poll_plex_availability" not in job_ids

    job_kwargs = {job["id"]: job for job in fake.jobs}
    assert job_kwargs["plex_recent_scan"]["kwargs"] == {"trigger_source": "scheduler"}
    assert job_kwargs["plex_poll"]["kwargs"] == {"trigger_source": "scheduler"}
    assert job_kwargs["check_download_completion"]["trigger"].interval.total_seconds() == 30
    assert fake.started is True


@pytest.mark.asyncio
async def test_poll_overseerr_uses_settings_service_import_helper(monkeypatch):
    """Overseerr polling should call the extracted settings import helper directly."""

    db = AsyncMock()
    runtime_settings = SimpleNamespace(overseerr_url="https://overseerr", overseerr_api_key="key")
    monkeypatch.setattr(scheduler_service, "get_settings", lambda: runtime_settings)

    import_requests = AsyncMock(return_value=(2, 1))
    monkeypatch.setattr(
        scheduler_service.overseerr_import_service,
        "import_overseerr_requests",
        import_requests,
    )

    logger = MagicMock()
    service = SchedulerService(lambda: _FakeSessionContext(db), logger=logger)

    await service._poll_overseerr()

    import_requests.assert_awaited_once_with(
        db,
        runtime_settings,
        overseerr_service_cls=scheduler_service.OverseerrService,
        plex_service_cls=scheduler_service.PlexService,
        evaluate_imported_request_func=scheduler_service.evaluate_imported_request,
        prepare_overseerr_import_func=scheduler_service.overseerr_import_service.prepare_overseerr_import,
        logger=logger,
    )
    logger.info.assert_called_once_with(
        "Overseerr poll: synced %d new request(s) (%d skipped/existing)",
        2,
        1,
    )


@pytest.mark.asyncio
async def test_download_completion_check_closes_plex_service_on_error(monkeypatch):
    """Download completion polling should always close PlexService."""
    db = AsyncMock()
    runtime_settings = SimpleNamespace()
    monkeypatch.setattr(scheduler_service, "get_settings", lambda: runtime_settings)

    plex_instance = AsyncMock()
    qbittorrent_instance = AsyncMock()
    plex_polling_instance = AsyncMock()
    download_completion_service = AsyncMock()
    download_completion_service.check_downloading_requests = AsyncMock(
        side_effect=RuntimeError("download boom")
    )

    monkeypatch.setattr(scheduler_service, "PlexService", lambda settings: plex_instance)
    monkeypatch.setattr(
        scheduler_service,
        "QbittorrentService",
        lambda settings: qbittorrent_instance,
    )
    monkeypatch.setattr(
        scheduler_service,
        "PlexPollingService",
        lambda db_session, plex: plex_polling_instance,
    )
    monkeypatch.setattr(
        "app.siftarr.services.download_completion_service.DownloadCompletionService",
        lambda db_session, qbittorrent, plex_polling: download_completion_service,
    )

    logger = MagicMock()
    service = SchedulerService(lambda: _FakeSessionContext(db), logger=logger)

    await service._check_download_completion()

    download_completion_service.check_downloading_requests.assert_awaited_once()
    plex_instance.close.assert_awaited_once()
    logger.exception.assert_called_once_with("Error during download completion check")


@pytest.mark.asyncio
async def test_recheck_unreleased_revisits_finished_and_available_tv_requests(monkeypatch):
    """Scheduler recheck should revisit ongoing TV rows beyond current unreleased ones."""
    db = AsyncMock()
    completed_tv = SimpleNamespace(id=1, media_type=MediaType.TV, status=RequestStatus.COMPLETED)
    available_tv = SimpleNamespace(id=2, media_type=MediaType.TV, status=RequestStatus.COMPLETED)

    lifecycle_service = AsyncMock()
    lifecycle_service.get_release_recheck_requests.return_value = [completed_tv, available_tv]
    monkeypatch.setattr(scheduler_service, "LifecycleService", lambda db_session: lifecycle_service)

    runtime_settings = SimpleNamespace()
    monkeypatch.setattr(scheduler_service, "get_settings", lambda: runtime_settings)

    overseerr_instance = AsyncMock()
    monkeypatch.setattr(scheduler_service, "OverseerrService", lambda settings: overseerr_instance)

    evaluator = AsyncMock()
    evaluator.evaluate_and_apply = AsyncMock(
        side_effect=[RequestStatus.UNRELEASED, RequestStatus.PENDING]
    )
    monkeypatch.setattr(
        scheduler_service, "UnreleasedEvaluator", lambda db_session, overseerr: evaluator
    )

    queue_service = AsyncMock()
    monkeypatch.setattr(scheduler_service, "PendingQueueService", lambda db_session: queue_service)

    service = SchedulerService(lambda: _FakeSessionContext(db), logger=MagicMock())
    await service._recheck_unreleased()

    lifecycle_service.get_release_recheck_requests.assert_awaited_once_with(limit=500)
    assert evaluator.evaluate_and_apply.await_count == 2
    queue_service.add_to_queue.assert_awaited_once_with(2)
    overseerr_instance.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_recent_plex_scan_returns_locked_when_job_already_running(monkeypatch):
    """Concurrent recent scan triggers should report lock contention."""

    db = AsyncMock()
    runtime_settings = SimpleNamespace()
    monkeypatch.setattr(scheduler_service, "get_settings", lambda: runtime_settings)

    release_scan = asyncio.Event()

    class FakePlexService:
        def __init__(self, settings):
            self.settings = settings
            self.close = AsyncMock()

    class FakePollingService:
        def __init__(self, db_session, plex):
            self.db_session = db_session
            self.plex = plex

        async def scan_recent(self):
            await release_scan.wait()
            return SimpleNamespace(
                completed_requests=2,
                metrics=SimpleNamespace(
                    as_dict=lambda: {
                        "scanned_items": 2,
                        "matched_requests": 2,
                        "skipped_on_error_items": 0,
                    }
                ),
                last_error=None,
            )

    monkeypatch.setattr(scheduler_service, "PlexService", FakePlexService)
    monkeypatch.setattr(scheduler_service, "PlexPollingService", FakePollingService)

    service = SchedulerService(lambda: _FakeSessionContext(db), logger=MagicMock())

    first_run = asyncio.create_task(service.trigger_recent_plex_scan_now())
    await asyncio.sleep(0)

    locked_result = await service.trigger_recent_plex_scan_now()
    assert locked_result.status == "locked"
    assert locked_result.job_name == PLEX_RECENT_SCAN_JOB_NAME
    assert locked_result.lock_owner is not None

    release_scan.set()
    completed_result = await first_run
    assert completed_result.status == "completed"
    assert completed_result.metrics_payload == {
        "completed_requests": 2,
        "scanned_items": 2,
        "matched_requests": 2,
        "skipped_on_error_items": 0,
    }

    snapshot = await service.get_plex_job_state_snapshot()
    recent_scan_state = snapshot[PLEX_RECENT_SCAN_JOB_NAME]
    assert recent_scan_state["locked"] is False
    assert recent_scan_state["last_success"] is not None
    assert recent_scan_state["last_run"] is not None
    assert recent_scan_state["last_started"] is not None
    assert recent_scan_state["last_error"] is None
    assert recent_scan_state["metrics_payload"] == completed_result.metrics_payload


@pytest.mark.asyncio
async def test_plex_poll_records_failed_run_state(monkeypatch):
    """Failed poll runs should persist in-memory error state."""

    db = AsyncMock()
    runtime_settings = SimpleNamespace()
    monkeypatch.setattr(scheduler_service, "get_settings", lambda: runtime_settings)

    plex_instance = AsyncMock()
    monkeypatch.setattr(scheduler_service, "PlexService", lambda settings: plex_instance)

    polling_service = MagicMock()
    polling_service.poll = AsyncMock(side_effect=RuntimeError("plex timeout"))
    monkeypatch.setattr(
        scheduler_service,
        "PlexPollingService",
        lambda db_session, plex: polling_service,
    )

    service = SchedulerService(lambda: _FakeSessionContext(db), logger=MagicMock())

    result = await service.trigger_plex_poll_now()

    assert result.status == "failed"
    assert result.job_name == PLEX_POLL_JOB_NAME
    assert result.error == "plex timeout"
    assert result.metrics_payload is None
    plex_instance.close.assert_awaited_once()

    snapshot = await service.get_plex_job_state_snapshot()
    poll_state = snapshot[PLEX_POLL_JOB_NAME]
    assert poll_state["locked"] is False
    assert poll_state["last_started"] is not None
    assert poll_state["last_run"] is not None
    assert poll_state["last_success"] is None
    assert poll_state["last_error"] == "plex timeout"
    assert poll_state["metrics_payload"] is None


@pytest.mark.asyncio
async def test_process_pending_item_marks_request_failed_after_max_retry_result(monkeypatch):
    """Non-completed retry results should consume the last retry and fail the request."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_maker = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with session_maker() as session:
        request = Request(
            external_id="retry-final-result",
            media_type=MediaType.MOVIE,
            title="Retry Movie",
            status=RequestStatus.PENDING,
            next_retry_at=datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=1),
            retry_count=1,
        )
        session.add(request)
        await session.commit()
        await session.refresh(request)

        monkeypatch.setattr(
            scheduler_service,
            "get_settings",
            lambda: SimpleNamespace(retry_interval_hours=24, max_retry_duration_days=2),
        )
        monkeypatch.setattr(scheduler_service, "ProwlarrService", lambda settings: MagicMock())
        monkeypatch.setattr(scheduler_service, "QbittorrentService", lambda settings: MagicMock())

        class FakeMovieDecisionService:
            def __init__(self, db, prowlarr, qbittorrent):
                self.process_request = AsyncMock(
                    return_value={"status": "pending", "message": "still blocked"}
                )

        monkeypatch.setattr(scheduler_service, "MovieDecisionService", FakeMovieDecisionService)

        service = SchedulerService(session_maker, logger=MagicMock())
        await service._process_pending_item(request)

    async with session_maker() as session:
        refreshed = await session.get(Request, request.id)
        assert refreshed is not None
        assert refreshed.status == RequestStatus.FAILED
        assert refreshed.next_retry_at is None
        assert refreshed.retry_count == 0
        assert refreshed.rejection_reason == "still blocked"

    await engine.dispose()


@pytest.mark.asyncio
async def test_process_pending_item_reschedules_retry_after_exception(monkeypatch):
    """Retry exceptions should preserve the error and reschedule when retries remain."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_maker = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    scheduled_before = datetime.now(UTC).replace(tzinfo=None)

    async with session_maker() as session:
        request = Request(
            external_id="retry-exception",
            media_type=MediaType.MOVIE,
            title="Retry Movie",
            status=RequestStatus.PENDING,
            next_retry_at=scheduled_before - timedelta(minutes=1),
            retry_count=0,
        )
        session.add(request)
        await session.commit()
        await session.refresh(request)

        monkeypatch.setattr(
            scheduler_service,
            "get_settings",
            lambda: SimpleNamespace(retry_interval_hours=24, max_retry_duration_days=3),
        )
        monkeypatch.setattr(scheduler_service, "ProwlarrService", lambda settings: MagicMock())
        monkeypatch.setattr(scheduler_service, "QbittorrentService", lambda settings: MagicMock())

        class FakeMovieDecisionService:
            def __init__(self, db, prowlarr, qbittorrent):
                self.process_request = AsyncMock(side_effect=RuntimeError("decision boom"))

        monkeypatch.setattr(scheduler_service, "MovieDecisionService", FakeMovieDecisionService)

        service = SchedulerService(session_maker, logger=MagicMock())
        await service._process_pending_item(request)

    async with session_maker() as session:
        refreshed = await session.get(Request, request.id)
        assert refreshed is not None
        assert refreshed.status == RequestStatus.PENDING
        assert refreshed.retry_count == 1
        assert refreshed.rejection_reason == "decision boom"
        assert refreshed.next_retry_at is not None
        assert refreshed.next_retry_at >= scheduled_before + timedelta(hours=23, minutes=59)

    await engine.dispose()
