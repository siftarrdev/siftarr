"""Tests for Plex scheduler jobs and manual triggers."""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.siftarr.models._base import Base
from app.siftarr.models.app_setting import AppSetting
from app.siftarr.models.episode import Episode
from app.siftarr.models.request import MediaType, Request, RequestStatus
from app.siftarr.models.season import Season
from app.siftarr.services.admin import scheduler_service
from app.siftarr.services.admin.scheduler_service import (
    PLEX_POLL_JOB_NAME,
    PLEX_RECENT_SCAN_JOB_NAME,
    SchedulerService,
)
from app.siftarr.services.admin.settings_service import (
    OVERSEERR_LAST_SYNC_SUCCESS_KEY,
    PLEX_LAST_SYNC_SUCCESS_KEY,
    SettingsStore,
    parse_sync_timestamp,
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
        scheduler_service,
        "get_settings",
        lambda: SimpleNamespace(
            overseerr_poll_interval_minutes=17,
            qbittorrent_completion_poll_interval_seconds=45,
            plex_fast_sync_interval_minutes=7,
            plex_full_sync_frequency="weekly",
            plex_full_sync_time="04:30",
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
    assert job_kwargs["poll_overseerr"]["trigger"].interval.total_seconds() == 17 * 60
    assert job_kwargs["plex_recent_scan"]["trigger"].interval.total_seconds() == 7 * 60
    assert job_kwargs["plex_recent_scan"]["kwargs"] == {"trigger_source": "scheduler"}
    assert job_kwargs["plex_poll"]["kwargs"] == {"trigger_source": "scheduler"}
    assert job_kwargs["check_download_completion"]["trigger"].interval.total_seconds() == 45
    assert "sun" in str(job_kwargs["plex_poll"]["trigger"])
    assert fake.started is True


@pytest.mark.asyncio
async def test_poll_overseerr_uses_settings_service_import_helper(monkeypatch):
    """Overseerr polling should call the extracted settings import helper directly."""

    db = AsyncMock()
    runtime_settings = SimpleNamespace(overseerr_url="https://overseerr", overseerr_api_key="key")
    monkeypatch.setattr(scheduler_service, "get_settings", lambda: runtime_settings)

    import_requests = AsyncMock(return_value=(2, 1))
    monkeypatch.setattr(
        scheduler_service.settings_service,
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
        prepare_overseerr_import_func=scheduler_service.settings_service.prepare_overseerr_import,
        logger=logger,
    )
    logger.info.assert_called_once_with(
        "Overseerr poll: synced %d new request(s) (%d skipped/existing)",
        2,
        1,
    )


@pytest.mark.asyncio
async def test_overseerr_poll_records_success_timestamp(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    runtime_settings = SimpleNamespace(overseerr_url="https://overseerr", overseerr_api_key="key")
    monkeypatch.setattr(scheduler_service, "get_settings", lambda: runtime_settings)
    monkeypatch.setattr(
        scheduler_service.settings_service,
        "import_overseerr_requests",
        AsyncMock(return_value=(0, 0)),
    )

    logger = MagicMock()
    service = SchedulerService(session_maker, logger=logger)
    await service._poll_overseerr()

    async with session_maker() as session:
        setting = await session.get(AppSetting, OVERSEERR_LAST_SYNC_SUCCESS_KEY)
        assert setting is not None
        assert parse_sync_timestamp(cast("str | None", setting.value)) is not None

    await engine.dispose()


@pytest.mark.asyncio
async def test_plex_poll_records_success_timestamp_and_skips_failed_or_locked(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    monkeypatch.setattr(scheduler_service, "get_settings", lambda: SimpleNamespace())
    poll = AsyncMock(return_value=0)

    class FakePlexService:
        def __init__(self, settings):
            self.settings = settings

    class FakePollingService:
        def __init__(self, db_session, plex):
            self.poll = poll

    monkeypatch.setattr(scheduler_service, "PlexService", FakePlexService)
    monkeypatch.setattr(scheduler_service, "PlexPollingService", FakePollingService)

    logger = MagicMock()
    service = SchedulerService(session_maker, logger=logger)
    assert (await service.trigger_plex_poll_now()).status == "completed"

    async with session_maker() as session:
        setting = await session.get(AppSetting, PLEX_LAST_SYNC_SUCCESS_KEY)
        assert setting is not None
        first_success = parse_sync_timestamp(cast("str | None", setting.value))
        assert first_success is not None

    poll.side_effect = RuntimeError("boom")
    assert (await service.trigger_plex_poll_now()).status == "failed"
    async with session_maker() as session:
        setting = await session.get(AppSetting, PLEX_LAST_SYNC_SUCCESS_KEY)
        assert setting is not None
        assert parse_sync_timestamp(cast("str | None", setting.value)) == first_success

    async with service._plex_job_state_guard:
        service._get_plex_job_state(PLEX_POLL_JOB_NAME).locked = True
    assert (await service.trigger_plex_poll_now()).status == "locked"
    async with session_maker() as session:
        setting = await session.get(AppSetting, PLEX_LAST_SYNC_SUCCESS_KEY)
        assert setting is not None
        assert parse_sync_timestamp(cast("str | None", setting.value)) == first_success

    await engine.dispose()


@pytest.mark.asyncio
async def test_plex_sign_in_sync_uses_full_poll_with_distinct_trigger(monkeypatch):
    """Plex admin sign-in sync should reuse the guarded full poll path."""
    service = SchedulerService(lambda: _FakeSessionContext(AsyncMock()), logger=MagicMock())
    run_poll = AsyncMock(return_value=SimpleNamespace(status="completed", error=None))
    monkeypatch.setattr(service, "_run_plex_poll_job", run_poll)

    result = await service.trigger_plex_sign_in_sync()

    assert result.status == "completed"
    run_poll.assert_awaited_once_with(trigger_source="plex_sign_in")


@pytest.mark.asyncio
async def test_startup_catchup_runs_overseerr_before_plex_when_both_stale(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    monkeypatch.setattr(
        scheduler_service,
        "get_settings",
        lambda: SimpleNamespace(
            overseerr_url="https://overseerr",
            overseerr_api_key="key",
            plex_url="https://plex",
            plex_token="token",
        ),
    )
    order = []
    logger = MagicMock()
    service = SchedulerService(session_maker, logger=logger)

    async def poll_overseerr():
        order.append("overseerr")

    async def poll_plex(*, trigger_source):
        order.append(f"plex:{trigger_source}")
        return SimpleNamespace(status="completed", error=None)

    monkeypatch.setattr(service, "_poll_overseerr", poll_overseerr)
    monkeypatch.setattr(service, "_run_plex_poll_job", poll_plex)

    await service.run_startup_catchup_syncs()

    assert order == ["overseerr", "plex:startup"]
    await engine.dispose()


@pytest.mark.asyncio
async def test_startup_catchup_runs_only_stale_service(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    now = datetime.now(UTC)
    async with session_maker() as session, session.begin():
        await SettingsStore(session).record_sync_success("overseerr", now)

    monkeypatch.setattr(
        scheduler_service,
        "get_settings",
        lambda: SimpleNamespace(
            overseerr_url="https://overseerr",
            overseerr_api_key="key",
            plex_url="https://plex",
            plex_token="token",
        ),
    )
    service = SchedulerService(session_maker, logger=MagicMock())
    overseerr = AsyncMock()
    plex = AsyncMock(return_value=SimpleNamespace(status="completed", error=None))
    monkeypatch.setattr(service, "_poll_overseerr", overseerr)
    monkeypatch.setattr(service, "_run_plex_poll_job", plex)

    await service.run_startup_catchup_syncs()

    overseerr.assert_not_awaited()
    plex.assert_awaited_once_with(trigger_source="startup")
    await engine.dispose()


@pytest.mark.asyncio
async def test_startup_catchup_skips_unconfigured_and_continues_after_overseerr_failure(
    monkeypatch,
):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    logger = MagicMock()
    service = SchedulerService(session_maker, logger=logger)
    plex = AsyncMock(return_value=SimpleNamespace(status="completed", error=None))
    overseerr = AsyncMock(side_effect=RuntimeError("overseerr boom"))
    monkeypatch.setattr(service, "_poll_overseerr", overseerr)
    monkeypatch.setattr(service, "_run_plex_poll_job", plex)

    monkeypatch.setattr(
        scheduler_service,
        "get_settings",
        lambda: SimpleNamespace(
            overseerr_url="",
            overseerr_api_key="",
            plex_url="https://plex",
            plex_token="token",
        ),
    )
    await service.run_startup_catchup_syncs()
    overseerr.assert_not_awaited()
    plex.assert_awaited_once_with(trigger_source="startup")

    plex.reset_mock()
    monkeypatch.setattr(
        scheduler_service,
        "get_settings",
        lambda: SimpleNamespace(
            overseerr_url="https://overseerr",
            overseerr_api_key="key",
            plex_url="https://plex",
            plex_token="token",
        ),
    )
    await service.run_startup_catchup_syncs()
    overseerr.assert_awaited_once()
    plex.assert_awaited_once_with(trigger_source="startup")
    logger.exception.assert_called_with("Startup catch-up: Overseerr sync failed")
    await engine.dispose()


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
        "app.siftarr.services.admin.scheduler_service.DownloadCompletionService",
        lambda db_session, qbittorrent, plex_polling: download_completion_service,
    )

    logger = MagicMock()
    service = SchedulerService(lambda: _FakeSessionContext(db), logger=logger)

    await service._check_download_completion()

    download_completion_service.check_downloading_requests.assert_awaited_once()
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


@pytest.mark.asyncio
async def test_recheck_unreleased_persists_tv_unreleased_and_pending_transitions(
    monkeypatch,
):
    """Scheduler recheck should persist unreleased and return to pending after release."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    today = date.today()
    queued_request_ids: list[int] = []

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    monkeypatch.setattr(scheduler_service, "get_settings", lambda: SimpleNamespace())

    class FakeOverseerrService:
        def __init__(self, settings):
            self.settings = settings

        async def get_media_details(self, media_type, tmdb_id):
            return {
                "firstAirDate": "2025-01-01",
                "status": "Returning Series",
            }

        async def close(self):
            return None

    class FakePendingQueueService:
        def __init__(self, db):
            self.db = db

        async def add_to_queue(self, request_id):
            queued_request_ids.append(request_id)

    monkeypatch.setattr(scheduler_service, "OverseerrService", FakeOverseerrService)
    monkeypatch.setattr(scheduler_service, "PendingQueueService", FakePendingQueueService)

    async with session_maker() as session:
        request = Request(
            external_id="ongoing-tv-recheck",
            media_type=MediaType.TV,
            tmdb_id=12345,
            title="Ongoing TV",
            status=RequestStatus.COMPLETED,
        )
        session.add(request)
        await session.flush()

        season = Season(
            request_id=request.id,
            season_number=1,
            status=RequestStatus.UNRELEASED,
        )
        session.add(season)
        await session.flush()

        session.add_all(
            [
                Episode(
                    season_id=season.id,
                    episode_number=1,
                    air_date=today - timedelta(days=14),
                    status=RequestStatus.COMPLETED,
                ),
                Episode(
                    season_id=season.id,
                    episode_number=2,
                    air_date=today - timedelta(days=7),
                    status=RequestStatus.COMPLETED,
                ),
                Episode(
                    season_id=season.id,
                    episode_number=3,
                    air_date=today + timedelta(days=7),
                    status=RequestStatus.UNRELEASED,
                ),
            ]
        )
        await session.commit()
        request_id = request.id

    service = SchedulerService(session_maker, logger=MagicMock())
    await service._recheck_unreleased()

    async with session_maker() as session:
        refreshed = await session.get(Request, request_id)
        assert refreshed is not None
        # Episode-centric: {COMPLETED, UNRELEASED} → PENDING (mixed state)
        assert refreshed.status == RequestStatus.PENDING

        future_episode = await session.scalar(
            select(Episode)
            .join(Season, Season.id == Episode.season_id)
            .where(
                Season.request_id == request_id,
                Episode.episode_number == 3,
            )
        )
        assert future_episode is not None
        future_episode.air_date = today - timedelta(days=1)
        future_episode.status = RequestStatus.COMPLETED
        await session.commit()

    await service._recheck_unreleased()

    async with session_maker() as session:
        refreshed = await session.get(Request, request_id)
        assert refreshed is not None
        # Episode-centric: all COMPLETED → COMPLETED
        assert refreshed.status == RequestStatus.COMPLETED

    assert queued_request_ids == [request_id]
    await engine.dispose()


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
                clean_run=True,
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
