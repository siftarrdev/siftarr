"""Tests for split Plex scheduler jobs and manual triggers."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.siftarr.models.request import MediaType, RequestStatus
from app.siftarr.services import scheduler_service
from app.siftarr.services.plex_polling_service import PlexPollingService
from app.siftarr.services.scheduler_service import (
    PLEX_FULL_RECONCILE_JOB_NAME,
    PLEX_INCREMENTAL_SYNC_JOB_NAME,
    PlexJobRunResult,
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
async def test_start_registers_split_plex_jobs(monkeypatch):
    """Scheduler startup should register separate incremental and full Plex jobs."""
    created = {}

    def fake_scheduler(**kwargs):
        created["scheduler"] = _FakeScheduler(**kwargs)
        return created["scheduler"]

    monkeypatch.setattr(scheduler_service, "AsyncIOScheduler", fake_scheduler)
    monkeypatch.setattr(
        "app.siftarr.config.get_settings",
        lambda: SimpleNamespace(
            plex_recent_scan_interval_minutes=5,
            plex_full_reconcile_interval_minutes=360,
        ),
    )

    service = SchedulerService(lambda: _FakeSessionContext(AsyncMock()), logger=MagicMock())
    service.start()

    fake = created["scheduler"]
    job_ids = {job["id"] for job in fake.jobs}
    assert "plex_incremental_sync" in job_ids
    assert "plex_full_reconcile" in job_ids
    assert "check_download_completion" in job_ids
    assert "poll_plex_availability" not in job_ids

    job_kwargs = {job["id"]: job for job in fake.jobs}
    assert job_kwargs["plex_incremental_sync"]["kwargs"] == {"trigger_source": "scheduler"}
    assert job_kwargs["plex_full_reconcile"]["kwargs"] == {"trigger_source": "scheduler"}
    assert job_kwargs["check_download_completion"]["trigger"].interval.total_seconds() == 30
    assert fake.started is True


@pytest.mark.asyncio
async def test_incremental_job_runs_under_persisted_lock_and_records_metrics(monkeypatch):
    """Incremental sync should acquire the persisted lock and store scan metrics."""
    db = AsyncMock()
    runtime_settings = SimpleNamespace(plex_recent_scan_interval_minutes=5)
    run_result = SimpleNamespace(
        mode="incremental_recent_scan",
        completed_requests=2,
        metrics=SimpleNamespace(
            as_dict=lambda: {
                "scanned_items": 4,
                "matched_requests": 2,
                "deduped_items": 1,
                "downgraded_requests": 0,
                "skipped_on_error_items": 0,
                "checkpoint": {
                    "previous_checkpoint_at": None,
                    "current_checkpoint_at": "2026-04-19T12:00:00",
                    "advanced": True,
                },
            }
        ),
    )

    monkeypatch.setattr(
        scheduler_service, "get_effective_settings", AsyncMock(return_value=runtime_settings)
    )

    state_service = MagicMock()
    state_service.recover_stale_lock = AsyncMock()
    state_service.acquire_lock = AsyncMock(return_value=SimpleNamespace(lock_owner="owner-a"))
    state_service.get_state = AsyncMock()
    state_service.release_lock = AsyncMock()
    monkeypatch.setattr(scheduler_service, "PlexScanStateService", lambda db_session: state_service)

    plex_instance = AsyncMock()
    monkeypatch.setattr(scheduler_service, "PlexService", lambda settings: plex_instance)

    polling_service = MagicMock()
    polling_service.incremental_recent_scan = AsyncMock(return_value=run_result)
    monkeypatch.setattr(
        scheduler_service, "PlexPollingService", lambda db_session, plex: polling_service
    )

    service = SchedulerService(lambda: _FakeSessionContext(db), logger=MagicMock())
    result = await service.trigger_incremental_plex_sync_now()

    assert result == PlexJobRunResult(
        job_name=PLEX_INCREMENTAL_SYNC_JOB_NAME,
        status="completed",
        completed_requests=2,
        metrics_payload={
            "mode": "incremental_recent_scan",
            "completed_requests": 2,
            "scan": {
                "scanned_items": 4,
                "matched_requests": 2,
                "deduped_items": 1,
                "downgraded_requests": 0,
                "skipped_on_error_items": 0,
                "checkpoint": {
                    "previous_checkpoint_at": None,
                    "current_checkpoint_at": "2026-04-19T12:00:00",
                    "advanced": True,
                },
            },
        },
    )
    state_service.recover_stale_lock.assert_awaited_once_with(PLEX_INCREMENTAL_SYNC_JOB_NAME)
    state_service.acquire_lock.assert_awaited_once()
    state_service.release_lock.assert_awaited_once()
    release_call = state_service.release_lock.await_args
    assert release_call is not None
    release_kwargs = release_call.kwargs
    assert release_kwargs["success"] is True
    assert release_kwargs["checkpoint_at"] == datetime.fromisoformat("2026-04-19T12:00:00")
    assert release_kwargs["metrics_payload"]["mode"] == "incremental_recent_scan"
    polling_service.incremental_recent_scan.assert_awaited_once_with(
        acquire_lock=False,
        previous_checkpoint_at=None,
    )
    plex_instance.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_incremental_job_recovers_stale_lock_before_running(monkeypatch):
    """Incremental scheduler runs should attempt stale-lock recovery before acquiring."""
    db = AsyncMock()
    runtime_settings = SimpleNamespace(plex_recent_scan_interval_minutes=5)
    run_result = SimpleNamespace(
        mode="incremental_recent_scan",
        completed_requests=0,
        metrics=SimpleNamespace(
            as_dict=lambda: {
                "scanned_items": 0,
                "matched_requests": 0,
                "deduped_items": 0,
                "downgraded_requests": 0,
                "skipped_on_error_items": 0,
                "checkpoint": {
                    "previous_checkpoint_at": None,
                    "current_checkpoint_at": "2026-04-19T12:00:00",
                    "advanced": True,
                },
            }
        ),
    )

    monkeypatch.setattr(
        scheduler_service, "get_effective_settings", AsyncMock(return_value=runtime_settings)
    )

    state_service = MagicMock()
    state_service.recover_stale_lock = AsyncMock(
        return_value=SimpleNamespace(lock_owner=None, checkpoint_at=None)
    )
    state_service.acquire_lock = AsyncMock(return_value=SimpleNamespace(lock_owner="owner-a"))
    state_service.get_state = AsyncMock()
    state_service.release_lock = AsyncMock()
    monkeypatch.setattr(scheduler_service, "PlexScanStateService", lambda db_session: state_service)

    plex_instance = AsyncMock()
    monkeypatch.setattr(scheduler_service, "PlexService", lambda settings: plex_instance)

    polling_service = MagicMock()
    polling_service.incremental_recent_scan = AsyncMock(return_value=run_result)
    monkeypatch.setattr(
        scheduler_service, "PlexPollingService", lambda db_session, plex: polling_service
    )

    service = SchedulerService(lambda: _FakeSessionContext(db), logger=MagicMock())
    result = await service.trigger_incremental_plex_sync_now()

    assert result.status == "completed"
    state_service.recover_stale_lock.assert_awaited_once_with(PLEX_INCREMENTAL_SYNC_JOB_NAME)
    state_service.acquire_lock.assert_awaited_once()


@pytest.mark.asyncio
async def test_incremental_job_persists_partial_outcome_without_marking_success(monkeypatch):
    """Scheduler-managed incremental partial runs should retain error state and checkpoint."""
    db = AsyncMock()
    runtime_settings = SimpleNamespace(plex_recent_scan_interval_minutes=5)
    previous_checkpoint = datetime.fromisoformat("2026-04-19T12:00:00+00:00")
    run_result = SimpleNamespace(
        mode="incremental_recent_scan",
        completed_requests=1,
        clean_run=False,
        last_error="Incremental recent Plex scan had transient request probe errors; checkpoint retained",
        metrics=SimpleNamespace(
            as_dict=lambda: {
                "scanned_items": 2,
                "matched_requests": 1,
                "deduped_items": 0,
                "downgraded_requests": 0,
                "skipped_on_error_items": 1,
                "checkpoint": {
                    "previous_checkpoint_at": "2026-04-19T12:00:00+00:00",
                    "current_checkpoint_at": "2026-04-19T12:00:00+00:00",
                    "advanced": False,
                },
            }
        ),
    )

    monkeypatch.setattr(
        scheduler_service, "get_effective_settings", AsyncMock(return_value=runtime_settings)
    )

    state_service = MagicMock()
    state_service.recover_stale_lock = AsyncMock()
    state_service.acquire_lock = AsyncMock(
        return_value=SimpleNamespace(lock_owner="owner-a", checkpoint_at=previous_checkpoint)
    )
    state_service.get_state = AsyncMock()
    state_service.release_lock = AsyncMock()
    monkeypatch.setattr(scheduler_service, "PlexScanStateService", lambda db_session: state_service)

    plex_instance = AsyncMock()
    monkeypatch.setattr(scheduler_service, "PlexService", lambda settings: plex_instance)

    polling_service = MagicMock()
    polling_service.incremental_recent_scan = AsyncMock(return_value=run_result)
    monkeypatch.setattr(
        scheduler_service, "PlexPollingService", lambda db_session, plex: polling_service
    )

    service = SchedulerService(lambda: _FakeSessionContext(db), logger=MagicMock())
    result = await service.trigger_incremental_plex_sync_now()

    assert result == PlexJobRunResult(
        job_name=PLEX_INCREMENTAL_SYNC_JOB_NAME,
        status="completed",
        completed_requests=1,
        metrics_payload={
            "mode": "incremental_recent_scan",
            "completed_requests": 1,
            "scan": {
                "scanned_items": 2,
                "matched_requests": 1,
                "deduped_items": 0,
                "downgraded_requests": 0,
                "skipped_on_error_items": 1,
                "checkpoint": {
                    "previous_checkpoint_at": "2026-04-19T12:00:00+00:00",
                    "current_checkpoint_at": "2026-04-19T12:00:00+00:00",
                    "advanced": False,
                },
            },
        },
    )
    release_call = state_service.release_lock.await_args
    assert release_call is not None
    release_kwargs = release_call.kwargs
    assert release_kwargs["success"] is False
    assert release_kwargs["checkpoint_at"] == previous_checkpoint
    assert release_kwargs["last_error"] == run_result.last_error
    polling_service.incremental_recent_scan.assert_awaited_once_with(
        acquire_lock=False,
        previous_checkpoint_at=previous_checkpoint,
    )
    plex_instance.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_incremental_job_uses_non_locking_inner_scan_path(monkeypatch):
    """Scheduler-managed incremental runs should not re-acquire the same persisted lock."""
    db = AsyncMock()
    req = MagicMock()
    req.id = 1
    req.media_type = scheduler_service.MediaType.MOVIE
    req.status = scheduler_service.RequestStatus.SEARCHING
    req.tmdb_id = 111
    req.tvdb_id = None
    req.title = "Movie A"
    req.seasons = []
    req.requested_episodes = None
    req.plex_rating_key = None

    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [req]
    db.execute.return_value = mock_result

    runtime_settings = SimpleNamespace(
        plex_recent_scan_interval_minutes=5,
        plex_sync_concurrency=4,
        plex_checkpoint_buffer_minutes=10,
    )
    monkeypatch.setattr(
        scheduler_service, "get_effective_settings", AsyncMock(return_value=runtime_settings)
    )

    previous_checkpoint = datetime.fromisoformat("2026-04-19T12:00:00+00:00")
    state_service = MagicMock()
    state_service.recover_stale_lock = AsyncMock()
    state_service.acquire_lock = AsyncMock(
        return_value=SimpleNamespace(lock_owner="owner-a", checkpoint_at=previous_checkpoint)
    )
    state_service.get_state = AsyncMock()
    state_service.release_lock = AsyncMock()
    monkeypatch.setattr(scheduler_service, "PlexScanStateService", lambda db_session: state_service)

    plex_instance = AsyncMock()

    async def iter_recently_added_items(media_type: str):
        if media_type == "movie":
            yield {
                "type": "movie",
                "rating_key": "movie-111",
                "title": "Movie A",
                "added_at": int(datetime(2026, 4, 19, 12, 1, tzinfo=UTC).timestamp()),
                "guids": ("tmdb://111",),
                "Media": [{"id": 1}],
            }
        if False:
            yield {}

    @asynccontextmanager
    async def scan_cycle():
        yield plex_instance

    plex_instance.settings = runtime_settings
    plex_instance.scan_cycle = scan_cycle
    plex_instance.iter_recently_added_items = iter_recently_added_items
    monkeypatch.setattr(scheduler_service, "PlexService", lambda settings: plex_instance)

    polling_instances: list[PlexPollingService] = []
    inner_acquire_lock_mock = AsyncMock(side_effect=AssertionError("inner lock used"))
    inner_release_lock_mock = AsyncMock(side_effect=AssertionError("inner release used"))
    transition_mock = AsyncMock(return_value=req)

    def build_polling_service(db_session, plex):
        service = PlexPollingService(db_session, plex)
        monkeypatch.setattr(service.scan_state, "acquire_lock", inner_acquire_lock_mock)
        monkeypatch.setattr(service.scan_state, "release_lock", inner_release_lock_mock)
        monkeypatch.setattr(service.lifecycle, "transition", transition_mock)
        polling_instances.append(service)
        return service

    monkeypatch.setattr(scheduler_service, "PlexPollingService", build_polling_service)

    service = SchedulerService(lambda: _FakeSessionContext(db), logger=MagicMock())
    result = await service.trigger_incremental_plex_sync_now()

    assert result.status == "completed"
    assert result.completed_requests == 1
    assert result.metrics_payload is not None
    assert result.metrics_payload["mode"] == "incremental_recent_scan"
    assert result.metrics_payload["completed_requests"] == 1
    assert len(polling_instances) == 1
    transition_mock.assert_awaited_once_with(
        1,
        scheduler_service.RequestStatus.COMPLETED,
        reason="Found on Plex",
    )


@pytest.mark.asyncio
async def test_full_reconcile_job_skips_when_lock_is_held(monkeypatch):
    """Full reconcile should report lock contention without running Plex work."""
    db = AsyncMock()
    runtime_settings = SimpleNamespace(plex_full_reconcile_interval_minutes=360)
    monkeypatch.setattr(
        scheduler_service, "get_effective_settings", AsyncMock(return_value=runtime_settings)
    )

    state_service = MagicMock()
    state_service.recover_stale_lock = AsyncMock()
    state_service.acquire_lock = AsyncMock(return_value=None)
    state_service.get_state = AsyncMock(return_value=SimpleNamespace(lock_owner="worker-b"))
    state_service.release_lock = AsyncMock()
    monkeypatch.setattr(scheduler_service, "PlexScanStateService", lambda db_session: state_service)

    plex_factory = MagicMock()
    monkeypatch.setattr(scheduler_service, "PlexService", plex_factory)

    service = SchedulerService(lambda: _FakeSessionContext(db), logger=MagicMock())
    result = await service.trigger_full_plex_reconcile_now()

    assert result == PlexJobRunResult(
        job_name=PLEX_FULL_RECONCILE_JOB_NAME,
        status="locked",
        lock_owner="worker-b",
    )
    plex_factory.assert_not_called()
    state_service.release_lock.assert_not_awaited()


@pytest.mark.asyncio
async def test_full_reconcile_job_records_failures_and_releases_lock(monkeypatch):
    """Failed full reconcile runs should persist the error and release the lock."""
    db = AsyncMock()
    runtime_settings = SimpleNamespace(plex_full_reconcile_interval_minutes=360)
    monkeypatch.setattr(
        scheduler_service, "get_effective_settings", AsyncMock(return_value=runtime_settings)
    )

    state_service = MagicMock()
    state_service.recover_stale_lock = AsyncMock()
    state_service.acquire_lock = AsyncMock(return_value=SimpleNamespace(lock_owner="owner-b"))
    state_service.get_state = AsyncMock()
    state_service.release_lock = AsyncMock()
    monkeypatch.setattr(scheduler_service, "PlexScanStateService", lambda db_session: state_service)

    plex_instance = AsyncMock()
    monkeypatch.setattr(scheduler_service, "PlexService", lambda settings: plex_instance)

    polling_service = MagicMock()
    polling_service.full_reconcile_scan = AsyncMock(side_effect=RuntimeError("plex boom"))
    monkeypatch.setattr(
        scheduler_service, "PlexPollingService", lambda db_session, plex: polling_service
    )

    service = SchedulerService(lambda: _FakeSessionContext(db), logger=MagicMock())
    result = await service.trigger_full_plex_reconcile_now()

    assert result == PlexJobRunResult(
        job_name=PLEX_FULL_RECONCILE_JOB_NAME,
        status="failed",
        error="plex boom",
    )
    state_service.release_lock.assert_awaited_once()
    release_call = state_service.release_lock.await_args
    assert release_call is not None
    release_kwargs = release_call.kwargs
    assert release_kwargs["success"] is False
    assert release_kwargs["last_error"] == "plex boom"
    plex_instance.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_legacy_poll_wrapper_delegates_to_incremental_job(monkeypatch):
    """Legacy poll compatibility entrypoint should delegate to the incremental scheduler job."""
    service = SchedulerService(lambda: _FakeSessionContext(AsyncMock()), logger=MagicMock())
    mock_run_incremental = AsyncMock(
        return_value=PlexJobRunResult(
            job_name=PLEX_INCREMENTAL_SYNC_JOB_NAME,
            status="completed",
            completed_requests=1,
        )
    )
    monkeypatch.setattr(service, "_run_incremental_plex_sync_job", mock_run_incremental)

    await service._poll_plex_availability()

    mock_run_incremental.assert_awaited_once_with(trigger_source="legacy_poll")


@pytest.mark.asyncio
async def test_poll_overseerr_uses_settings_service_import_helper(monkeypatch):
    """Overseerr polling should call the extracted settings import helper directly."""

    db = AsyncMock()
    runtime_settings = SimpleNamespace(overseerr_url="https://overseerr", overseerr_api_key="key")
    monkeypatch.setattr(
        scheduler_service, "get_effective_settings", AsyncMock(return_value=runtime_settings)
    )

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
    monkeypatch.setattr(
        scheduler_service, "get_effective_settings", AsyncMock(return_value=runtime_settings)
    )

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
    available_tv = SimpleNamespace(id=2, media_type=MediaType.TV, status=RequestStatus.AVAILABLE)

    lifecycle_service = AsyncMock()
    lifecycle_service.get_release_recheck_requests.return_value = [completed_tv, available_tv]
    monkeypatch.setattr(scheduler_service, "LifecycleService", lambda db_session: lifecycle_service)

    runtime_settings = SimpleNamespace()
    monkeypatch.setattr(
        scheduler_service, "get_effective_settings", AsyncMock(return_value=runtime_settings)
    )

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
