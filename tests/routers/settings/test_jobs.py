"""Settings manual and scheduler action tests."""

from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest

import app.siftarr.main as main_module
from app.siftarr.models.request import MediaType
from app.siftarr.routers import settings
from app.siftarr.services.admin import settings_service


@pytest.mark.asyncio
async def test_scheduler_settings_save_coerces_values_and_reset_clears_keys(monkeypatch, mock_db):
    store = MagicMock()
    store.set = AsyncMock()
    monkeypatch.setattr(settings, "SettingsStore", lambda db: store)
    monkeypatch.setattr(settings, "_restart_scheduler_if_running", MagicMock())

    response = await settings.save_scheduler_settings(
        MagicMock(),
        db=mock_db,
        overseerr_poll_interval_minutes="0",
        qbittorrent_completion_poll_interval_seconds="bad",
        plex_fast_sync_interval_minutes="9",
        plex_full_sync_frequency="weekly",
        plex_full_sync_time="4:05",
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/settings?scheduler_saved=true"
    store.set.assert_any_await("overseerr_poll_interval_minutes", "1")
    store.set.assert_any_await("qbittorrent_completion_poll_interval_seconds", "30")
    store.set.assert_any_await("plex_fast_sync_interval_minutes", "9")
    store.set.assert_any_await("plex_full_sync_frequency", "weekly")
    store.set.assert_any_await("plex_full_sync_time", "04:05")
    mock_db.commit.assert_awaited_once()

    clear = AsyncMock()
    monkeypatch.setattr(settings, "_clear_runtime_settings", clear)
    reset = await settings.reset_scheduler_settings(MagicMock(), db=mock_db)

    assert reset.status_code == 303
    assert clear.await_args is not None
    cleared_keys = clear.await_args.args
    assert "overseerr_poll_interval_minutes" in cleared_keys
    assert "plex_full_sync_time" in cleared_keys


@pytest.mark.asyncio
async def test_save_qbit_move_settings_persists_without_scheduler_restart(monkeypatch, mock_db):
    store = MagicMock()
    store.set = AsyncMock()
    monkeypatch.setattr(settings, "SettingsStore", lambda db: store)
    restart = MagicMock()
    monkeypatch.setattr(settings, "_restart_scheduler_if_running", restart)

    response = await settings.save_qbit_move_settings(
        MagicMock(),
        db=mock_db,
        qbittorrent_move_enabled="on",
        qbittorrent_move_completed_dir="/done",
        qbittorrent_move_movie_root="/movies",
        qbittorrent_move_tv_root="/tv",
        qbittorrent_move_unmanaged_fallback_enabled=None,
        qbittorrent_move_retention_weeks="0",
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/settings?qbit_move_saved=true"
    store.set.assert_any_await("qbittorrent_move_enabled", "true")
    store.set.assert_any_await("qbittorrent_move_completed_dir", "/done")
    store.set.assert_any_await("qbittorrent_move_movie_root", "/movies")
    store.set.assert_any_await("qbittorrent_move_tv_root", "/tv")
    store.set.assert_any_await("qbittorrent_move_unmanaged_fallback_enabled", "false")
    store.set.assert_any_await("qbittorrent_move_retention_weeks", "1")
    mock_db.commit.assert_awaited_once()
    restart.assert_not_called()


@pytest.mark.asyncio
async def test_export_settings_backup_returns_download(monkeypatch, mock_db):
    store = MagicMock()
    store.export_backup = AsyncMock(return_value={"version": 1, "settings": {"tz": "UTC"}})
    monkeypatch.setattr(settings, "SettingsStore", lambda db: store)

    response = await settings.export_settings_backup(db=mock_db)

    assert response.media_type == "application/json"
    assert "siftarr-settings-backup.json" in response.headers["content-disposition"]
    assert b'"tz": "UTC"' in response.body


@pytest.mark.asyncio
async def test_preview_settings_backup_renders_safe_validation_error(
    monkeypatch, mock_db, base_context
):
    monkeypatch.setattr(
        settings, "_build_settings_page_context", AsyncMock(return_value=base_context())
    )
    store = MagicMock()
    store.preview_backup = AsyncMock(side_effect=settings.SettingsBackupError("bad <secret>"))
    monkeypatch.setattr(settings, "SettingsStore", lambda db: store)
    upload = MagicMock()
    upload.read = AsyncMock(return_value=b"not-json")

    response = await settings.preview_settings_backup(MagicMock(), db=mock_db, backup_file=upload)
    body = cast(bytes, response.body).decode()

    assert "bad &lt;secret&gt;" in body
    assert "bad <secret>" not in body


@pytest.mark.asyncio
async def test_restore_settings_backup_applies_replace_mode(monkeypatch, mock_db, base_context):
    monkeypatch.setattr(
        settings, "_build_settings_page_context", AsyncMock(return_value=base_context())
    )
    store = MagicMock()
    store.restore_backup = AsyncMock(return_value={"mode": "replace", "restored": 2})
    monkeypatch.setattr(settings, "SettingsStore", lambda db: store)
    upload = MagicMock()
    upload.read = AsyncMock(return_value=b"{}")

    response = await settings.restore_settings_backup(
        MagicMock(), db=mock_db, mode="replace", backup_file=upload
    )
    context = cast(dict, getattr(response, "context", None))

    store.restore_backup.assert_awaited_once_with(b"{}", mode="replace")
    mock_db.commit.assert_awaited_once()
    assert context["message"] == "Restored 2 setting(s)."
    assert context["message_type"] == "success"


@pytest.fixture
def scheduler(monkeypatch, base_context):
    """Patch the page context and the scheduler singleton used by manual job routes."""
    monkeypatch.setattr(
        settings, "_build_settings_page_context", AsyncMock(return_value=base_context())
    )
    scheduler = MagicMock()
    monkeypatch.setattr(main_module, "scheduler_service", scheduler)
    return scheduler


def _recent_scan_metrics(completed, scanned, matched, skipped):
    return {
        "completed_requests": completed,
        "scanned_items": scanned,
        "matched_requests": matched,
        "skipped_on_error_items": skipped,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("route", "trigger", "result", "message"),
    [
        (
            "run_recent_plex_scan",
            "trigger_recent_plex_scan_now",
            {"completed_requests": 3, "metrics_payload": _recent_scan_metrics(3, 3, 3, 0)},
            "Recent Plex scan completed. Transitioned 3 request(s). "
            "Recent scan completed; completed 3, matched 3, scanned 3.",
        ),
        (
            "run_recent_plex_scan",
            "trigger_recent_plex_scan_now",
            {"completed_requests": 1, "metrics_payload": _recent_scan_metrics(1, 2, 1, 1)},
            "Recent Plex scan completed partially. Transitioned 1 request(s). "
            "Completed 1, matched 1, scanned 2, errors 1.",
        ),
        (
            "run_plex_poll",
            "trigger_plex_poll_now",
            {"completed_requests": 2, "metrics_payload": {"completed_requests": 2}},
            "Plex poll completed. Transitioned 2 request(s).",
        ),
        (
            "run_qbit_move",
            "trigger_download_completion_now",
            {"moved": 2, "removed": 1, "errors": 0},
            "qBittorrent move/retention completed. Moved 2, removed 1, errors 0.",
        ),
    ],
)
async def test_manual_job_routes_report_scheduler_result(
    scheduler, mock_db, route, trigger, result, message
):
    trigger_mock = AsyncMock(return_value=MagicMock(status="completed", error=None, **result))
    setattr(scheduler, trigger, trigger_mock)

    response = await getattr(settings, route)(MagicMock(), db=mock_db)
    context = cast(dict, getattr(response, "context", None))

    assert context["message_type"] == "success"
    assert context["message"] == message
    trigger_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_plex_poll_reports_lock_contention(scheduler, mock_db):
    """Manual Plex poll should surface lock contention cleanly."""
    scheduler.trigger_plex_poll_now = AsyncMock(
        return_value=MagicMock(
            status="locked",
            completed_requests=0,
            error=None,
            lock_owner="worker-1",
            metrics_payload=None,
        )
    )

    response = await settings.run_plex_poll(MagicMock(), db=mock_db)
    context = cast(dict, getattr(response, "context", None))

    assert context["message_type"] == "error"
    assert context["message"] == "Plex poll is already in progress."
    scheduler.trigger_plex_poll_now.assert_awaited_once()


@pytest.mark.asyncio
async def test_rescan_plex_route_reports_success(monkeypatch, mock_db, base_context):
    """Manual Plex rescan should report how many requests were completed."""

    monkeypatch.setattr(
        settings,
        "_build_settings_page_context",
        AsyncMock(return_value=base_context()),
    )
    monkeypatch.setattr(
        settings,
        "get_settings",
        lambda: MagicMock(),
    )
    plex_service = AsyncMock()
    monkeypatch.setattr(settings, "PlexService", lambda settings: plex_service)

    tv_request = MagicMock()
    tv_request.id = 12
    tv_request.media_type = MediaType.TV
    tv_request.status = "pending"
    scalars = MagicMock()
    scalars.all.return_value = [tv_request]
    execute_result = MagicMock()
    execute_result.scalars.return_value = scalars
    mock_db.execute.return_value = execute_result

    worker_db = AsyncMock()

    class FakeWorkerSessionContext:
        async def __aenter__(self):
            return worker_db

        async def __aexit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(
        "app.siftarr.database.async_session_maker", lambda: FakeWorkerSessionContext()
    )

    created_episode_sync = {}

    class FakeEpisodeSyncService:
        def __init__(self, db, overseerr=None, plex=None):
            created_episode_sync["db"] = db
            created_episode_sync["plex"] = plex

        async def sync_request(self, request_id):
            assert request_id == 12

    import app.siftarr.services.lifecycle.episode_sync_service as episode_sync_module

    monkeypatch.setattr(episode_sync_module, "EpisodeSyncService", FakeEpisodeSyncService)

    polling = AsyncMock()
    polling.get_active_requests = AsyncMock(return_value=[tv_request])
    polling.poll.return_value = 3
    monkeypatch.setattr(settings_service, "PlexPollingService", lambda db, plex: polling)

    response = await settings.rescan_plex(MagicMock(), db=mock_db)
    context = cast(dict, getattr(response, "context", None))

    assert context["message_type"] == "success"
    assert "Manual Plex rescan completed." in context["message"]
    assert "Re-synced 1 TV request(s)" in context["message"]
    assert "had 0 failed TV request(s)" in context["message"]
    assert "transitioned 3 request(s) to completed" in context["message"]
    assert created_episode_sync["db"] is worker_db
    assert created_episode_sync["plex"] is plex_service
