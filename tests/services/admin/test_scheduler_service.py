"""Tests for Plex scheduler jobs and manual triggers."""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import select

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
from tests.factories import make_episode, make_request, make_season
from tests.services.admin.conftest import FakeSessionContext


@pytest.mark.parametrize(
    ("completion_poll_seconds", "frequency", "expected_completion_seconds"),
    [
        pytest.param(45, "weekly", 45, id="configured_interval"),
        pytest.param(5, "daily", 30, id="clamped_for_targeted_plex_wait"),
    ],
)
def test_start_registers_jobs(
    monkeypatch,
    fake_scheduler,
    session_factory_for,
    completion_poll_seconds,
    frequency,
    expected_completion_seconds,
):
    monkeypatch.setattr(
        scheduler_service,
        "get_settings",
        lambda: SimpleNamespace(
            overseerr_poll_interval_minutes=17,
            qbittorrent_completion_poll_interval_seconds=completion_poll_seconds,
            plex_fast_sync_interval_minutes=7,
            plex_full_sync_frequency=frequency,
            plex_full_sync_time="04:30",
        ),
    )

    SchedulerService(session_factory_for(AsyncMock()), logger=MagicMock()).start()

    jobs = {job["id"]: job for job in fake_scheduler.jobs}
    assert "poll_plex_availability" not in jobs
    assert jobs["poll_overseerr"]["trigger"].interval.total_seconds() == 17 * 60
    assert jobs["plex_recent_scan"]["trigger"].interval.total_seconds() == 7 * 60
    assert jobs["plex_recent_scan"]["kwargs"] == {"trigger_source": "scheduler"}
    assert jobs["plex_poll"]["kwargs"] == {"trigger_source": "scheduler"}
    assert (
        jobs["check_download_completion"]["trigger"].interval.total_seconds()
        == expected_completion_seconds
    )
    if frequency == "weekly":
        assert "sun" in str(jobs["plex_poll"]["trigger"])
    assert fake_scheduler.started is True


@pytest.fixture
def fake_plex_poll(monkeypatch):
    """Fake Plex and polling services. Returns the AsyncMock used for poll()."""
    poll = AsyncMock(return_value=3)

    class FakePlexService:
        def __init__(self, settings):
            self.settings = settings

    class FakePollingService:
        def __init__(self, db_session, plex):
            self.poll = poll

    monkeypatch.setattr(scheduler_service, "get_settings", lambda: SimpleNamespace())
    monkeypatch.setattr(scheduler_service, "PlexService", FakePlexService)
    monkeypatch.setattr(scheduler_service, "PlexPollingService", FakePollingService)
    return poll


async def _last_sync(session_maker, key):
    async with session_maker() as session:
        setting = await session.get(AppSetting, key)
        assert setting is not None
        return parse_sync_timestamp(cast("str | None", setting.value))


async def test_plex_poll_records_success_timestamp_and_skips_failed_or_locked(
    session_maker, fake_plex_poll
):
    service = SchedulerService(session_maker, logger=MagicMock())

    result = await service.trigger_plex_poll_now()
    assert (result.status, result.completed_requests) == ("completed", 3)
    fake_plex_poll.assert_awaited_once_with(priority_only=False)
    first_success = await _last_sync(session_maker, PLEX_LAST_SYNC_SUCCESS_KEY)
    assert first_success is not None

    fake_plex_poll.side_effect = RuntimeError("boom")
    assert (await service.trigger_plex_poll_now()).status == "failed"
    assert await _last_sync(session_maker, PLEX_LAST_SYNC_SUCCESS_KEY) == first_success

    async with service._plex_job_state_guard:
        service._get_plex_job_state(PLEX_POLL_JOB_NAME).locked = True
    assert (await service.trigger_plex_poll_now()).status == "locked"
    assert await _last_sync(session_maker, PLEX_LAST_SYNC_SUCCESS_KEY) == first_success


async def test_plex_sign_in_sync_runs_guarded_full_poll(session_maker, fake_plex_poll):
    result = await SchedulerService(session_maker, logger=MagicMock()).trigger_plex_sign_in_sync()

    assert result.status == "completed"
    assert result.completed_requests == 3
    fake_plex_poll.assert_awaited_once_with(priority_only=False)


async def test_overseerr_poll_records_success_timestamp(monkeypatch, session_maker):
    monkeypatch.setattr(
        scheduler_service,
        "get_settings",
        lambda: SimpleNamespace(overseerr_url="https://overseerr", overseerr_api_key="key"),
    )
    import_requests = AsyncMock(return_value=(2, 1))
    monkeypatch.setattr(
        scheduler_service.settings_service, "import_overseerr_requests", import_requests
    )
    logger = MagicMock()

    await SchedulerService(session_maker, logger=logger)._poll_overseerr()

    import_requests.assert_awaited_once()
    assert await _last_sync(session_maker, OVERSEERR_LAST_SYNC_SUCCESS_KEY) is not None
    logger.info.assert_called_once_with(
        "Overseerr poll: synced %d new request(s) (%d skipped/existing)", 2, 1
    )


@pytest.mark.parametrize(
    ("overseerr_url", "overseerr_fresh", "overseerr_fails", "expected_calls"),
    [
        pytest.param("https://overseerr", False, False, ["overseerr", "plex"], id="both_stale"),
        pytest.param("https://overseerr", True, False, ["plex"], id="overseerr_fresh"),
        pytest.param("", False, False, ["plex"], id="overseerr_unconfigured"),
        pytest.param(
            "https://overseerr",
            False,
            True,
            ["overseerr", "plex"],
            id="overseerr_failure_continues",
        ),
    ],
)
async def test_startup_catchup(
    monkeypatch, session_maker, overseerr_url, overseerr_fresh, overseerr_fails, expected_calls
):
    if overseerr_fresh:
        async with session_maker() as session, session.begin():
            await SettingsStore(session).record_sync_success("overseerr", datetime.now(UTC))
    monkeypatch.setattr(
        scheduler_service,
        "get_settings",
        lambda: SimpleNamespace(
            overseerr_url=overseerr_url,
            overseerr_api_key="key" if overseerr_url else "",
            plex_url="https://plex",
            plex_token="token",
        ),
    )
    logger = MagicMock()
    service = SchedulerService(session_maker, logger=logger)
    calls: list[str] = []

    async def poll_overseerr():
        calls.append("overseerr")
        if overseerr_fails:
            raise RuntimeError("overseerr boom")

    async def poll_plex(*, trigger_source):
        calls.append("plex")
        assert trigger_source == "startup"
        return SimpleNamespace(status="completed", error=None)

    monkeypatch.setattr(service, "_poll_overseerr", poll_overseerr)
    monkeypatch.setattr(service, "_run_plex_poll_job", poll_plex)

    await service.run_startup_catchup_syncs()

    assert calls == expected_calls
    if overseerr_fails:
        logger.exception.assert_called_with("Startup catch-up: Overseerr sync failed")


async def test_download_completion_check_closes_plex_service_on_error(
    monkeypatch, session_factory_for
):
    """Download completion polling should always close PlexService."""
    monkeypatch.setattr(scheduler_service, "get_settings", lambda: SimpleNamespace())

    download_health_service = AsyncMock()
    download_health_service.observe.return_value = {"qbit_available": True, "problems": []}
    download_completion_service = AsyncMock()
    download_completion_service.check_downloading_requests = AsyncMock(
        side_effect=RuntimeError("download boom")
    )
    monkeypatch.setattr(scheduler_service, "PlexService", lambda settings: AsyncMock())
    monkeypatch.setattr(scheduler_service, "QbittorrentService", lambda settings: AsyncMock())
    monkeypatch.setattr(
        scheduler_service, "PlexPollingService", lambda db_session, plex: AsyncMock()
    )
    monkeypatch.setattr(
        scheduler_service,
        "DownloadHealthService",
        lambda db_session, qbittorrent: download_health_service,
    )
    monkeypatch.setattr(
        scheduler_service,
        "DownloadCompletionService",
        lambda db_session, qbittorrent, plex_polling: download_completion_service,
    )

    logger = MagicMock()
    service = SchedulerService(session_factory_for(AsyncMock()), logger=logger)

    await service._check_download_completion()

    logger.exception.assert_called_once_with("Error during download completion check")


@pytest.mark.parametrize("move_enabled", [True, False], ids=["move_enabled", "move_disabled"])
async def test_download_completion_runs_qbit_move_only_when_enabled(
    monkeypatch, session_factory_for, move_enabled
):
    monkeypatch.setattr(
        scheduler_service,
        "get_settings",
        lambda: SimpleNamespace(qbittorrent_move_enabled=move_enabled),
    )
    download_completion_service = AsyncMock()
    download_completion_service.check_downloading_requests.return_value = 0
    move_service = AsyncMock()
    move_service.run.return_value = SimpleNamespace(moved=1, removed=2, errors=0)

    monkeypatch.setattr(scheduler_service, "PlexService", lambda settings: AsyncMock())
    monkeypatch.setattr(scheduler_service, "QbittorrentService", lambda settings: AsyncMock())
    monkeypatch.setattr(
        scheduler_service, "PlexPollingService", lambda db_session, plex: AsyncMock()
    )
    monkeypatch.setattr(
        scheduler_service,
        "DownloadCompletionService",
        lambda db_session, qbittorrent, plex_polling: download_completion_service,
    )
    monkeypatch.setattr(
        scheduler_service,
        "QbitMoveService",
        lambda db_session, qbittorrent, settings, log: move_service,
    )

    service = SchedulerService(session_factory_for(AsyncMock()), logger=MagicMock())
    await service._check_download_completion()

    download_completion_service.check_downloading_requests.assert_awaited_once()
    assert move_service.run.await_count == (1 if move_enabled else 0)


async def test_recheck_unreleased_persists_tv_unreleased_and_pending_transitions(
    monkeypatch, session_maker, db
):
    """Scheduler recheck should persist unreleased and return to pending after release."""
    today = date.today()
    queued_request_ids: list[int] = []
    monkeypatch.setattr(scheduler_service, "get_settings", lambda: SimpleNamespace())

    class FakeOverseerrService:
        def __init__(self, settings):
            self.settings = settings

        async def get_media_details(self, media_type, tmdb_id):
            return {"firstAirDate": "2025-01-01", "status": "Returning Series"}

        async def close(self):
            return None

    class FakePendingQueueService:
        def __init__(self, db):
            self.db = db

        async def add_to_queue(self, request_id):
            queued_request_ids.append(request_id)

    monkeypatch.setattr(scheduler_service, "OverseerrService", FakeOverseerrService)
    monkeypatch.setattr(scheduler_service, "PendingQueueService", FakePendingQueueService)

    request = await make_request(
        db, media_type=MediaType.TV, tmdb_id=12345, status=RequestStatus.COMPLETED
    )
    season = await make_season(db, request, 1, status=RequestStatus.UNRELEASED)
    for number, offset, status in [
        (1, -14, RequestStatus.COMPLETED),
        (2, -7, RequestStatus.COMPLETED),
        (3, 7, RequestStatus.UNRELEASED),
    ]:
        await make_episode(
            db, season, number, air_date=today + timedelta(days=offset), status=status
        )
    await db.commit()

    service = SchedulerService(session_maker, logger=MagicMock())
    await service._recheck_unreleased()

    async def episode_three(session):
        return await session.scalar(
            select(Episode)
            .join(Season, Season.id == Episode.season_id)
            .where(Season.request_id == request.id, Episode.episode_number == 3)
        )

    async with session_maker() as session:
        refreshed = await session.get(Request, request.id)
        assert refreshed is not None
        # Episode-centric: {COMPLETED, UNRELEASED} -> UNRELEASED (no true pending work)
        assert refreshed.status == RequestStatus.UNRELEASED

        future_episode = await episode_three(session)
        assert future_episode is not None
        future_episode.air_date = today - timedelta(days=1)
        await session.commit()

    await service._recheck_unreleased()

    async with session_maker() as session:
        refreshed = await session.get(Request, request.id)
        assert refreshed is not None
        # Episode-centric: aired UNRELEASED episode becomes PENDING and queues search
        assert refreshed.status == RequestStatus.PENDING
        released_episode = await episode_three(session)
        assert released_episode is not None
        assert released_episode.status == RequestStatus.PENDING

    assert queued_request_ids == [request.id]


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

    service = SchedulerService(lambda: FakeSessionContext(db), logger=MagicMock())

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

    service = SchedulerService(lambda: FakeSessionContext(db), logger=MagicMock())

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


@pytest.mark.parametrize(
    ("retry_count", "decision", "expected_status", "expected_retry_count", "expected_reason"),
    [
        pytest.param(
            1,
            {"status": "pending", "message": "still blocked"},
            RequestStatus.FAILED,
            0,
            "still blocked",
            id="max_retry_result_fails_request",
        ),
        pytest.param(
            0,
            RuntimeError("decision boom"),
            RequestStatus.PENDING,
            1,
            "decision boom",
            id="exception_reschedules_retry",
        ),
    ],
)
async def test_process_pending_item_retry_outcomes(
    monkeypatch,
    session_maker,
    db,
    retry_count,
    decision,
    expected_status,
    expected_retry_count,
    expected_reason,
):
    now = datetime.now(UTC).replace(tzinfo=None)
    request = await make_request(
        db, next_retry_at=now - timedelta(minutes=1), retry_count=retry_count
    )
    await db.commit()

    monkeypatch.setattr(
        scheduler_service,
        "get_settings",
        lambda: SimpleNamespace(retry_interval_hours=24, max_retry_duration_days=2),
    )
    monkeypatch.setattr(scheduler_service, "ProwlarrService", lambda settings: MagicMock())
    monkeypatch.setattr(scheduler_service, "QbittorrentService", lambda settings: MagicMock())

    class FakeMovieDecisionService:
        def __init__(self, db, prowlarr, qbittorrent):
            if isinstance(decision, Exception):
                self.process_request = AsyncMock(side_effect=decision)
            else:
                self.process_request = AsyncMock(return_value=decision)

    monkeypatch.setattr(scheduler_service, "MovieDecisionService", FakeMovieDecisionService)

    await SchedulerService(session_maker, logger=MagicMock())._process_pending_item(request)

    async with session_maker() as session:
        refreshed = await session.get(Request, request.id)
        assert refreshed is not None
        assert refreshed.status == expected_status
        assert refreshed.retry_count == expected_retry_count
        assert refreshed.rejection_reason == expected_reason
        if expected_status == RequestStatus.FAILED:
            assert refreshed.next_retry_at is None
        else:
            assert refreshed.next_retry_at is not None
            assert refreshed.next_retry_at >= now + timedelta(hours=23, minutes=59)
