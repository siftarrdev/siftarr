"""Settings manual and scheduler action tests."""

from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.siftarr.models.request import MediaType
from app.siftarr.routers import settings


@pytest.mark.asyncio
async def test_run_incremental_plex_sync_reports_success(monkeypatch, mock_db, base_context):
    """Manual recent Plex scan should report scheduler success."""

    monkeypatch.setattr(
        settings,
        "_build_settings_page_context",
        AsyncMock(return_value=base_context()),
    )
    monkeypatch.setattr(settings, "_build_plex_job_statuses", AsyncMock(return_value=[]))

    scheduler = MagicMock()
    scheduler.trigger_incremental_plex_sync_now = AsyncMock(
        return_value=MagicMock(
            status="completed",
            completed_requests=3,
            error=None,
            metrics_payload={
                "completed_requests": 3,
                "scanned_items": 3,
                "matched_requests": 3,
                "skipped_on_error_items": 0,
            },
        )
    )

    import app.siftarr.main as main_module

    monkeypatch.setattr(main_module, "scheduler_service", scheduler)

    response = await settings.run_incremental_plex_sync(MagicMock(), db=mock_db)
    context = cast(dict, getattr(response, "context", None))

    assert context["message_type"] == "success"
    assert context["message"] == (
        "Recent Plex scan completed. Transitioned 3 request(s). "
        "Recent scan completed; completed 3, matched 3, scanned 3."
    )
    scheduler.trigger_incremental_plex_sync_now.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_incremental_plex_sync_reports_partial_completion(
    monkeypatch, mock_db, base_context
):
    """Manual recent Plex scan should describe partial completion."""

    monkeypatch.setattr(
        settings,
        "_build_settings_page_context",
        AsyncMock(return_value=base_context()),
    )
    monkeypatch.setattr(settings, "_build_plex_job_statuses", AsyncMock(return_value=[]))

    scheduler = MagicMock()
    scheduler.trigger_incremental_plex_sync_now = AsyncMock(
        return_value=MagicMock(
            status="completed",
            completed_requests=1,
            error=None,
            metrics_payload={
                "completed_requests": 1,
                "scanned_items": 2,
                "matched_requests": 1,
                "skipped_on_error_items": 1,
            },
        )
    )

    import app.siftarr.main as main_module

    monkeypatch.setattr(main_module, "scheduler_service", scheduler)

    response = await settings.run_incremental_plex_sync(MagicMock(), db=mock_db)
    context = cast(dict, getattr(response, "context", None))

    assert context["message_type"] == "success"
    assert context["message"] == (
        "Recent Plex scan completed partially. Transitioned 1 request(s). "
        "Completed 1, matched 1, scanned 2, errors 1."
    )


@pytest.mark.asyncio
async def test_run_full_plex_reconcile_reports_guarded_negative_reconciliation(
    monkeypatch, mock_db, base_context
):
    """Manual Plex poll should report poll completion."""

    monkeypatch.setattr(
        settings,
        "_build_settings_page_context",
        AsyncMock(return_value=base_context()),
    )
    monkeypatch.setattr(settings, "_build_plex_job_statuses", AsyncMock(return_value=[]))

    scheduler = MagicMock()
    scheduler.trigger_full_plex_reconcile_now = AsyncMock(
        return_value=MagicMock(
            status="completed",
            completed_requests=2,
            error=None,
            metrics_payload={"completed_requests": 2},
        )
    )

    import app.siftarr.main as main_module

    monkeypatch.setattr(main_module, "scheduler_service", scheduler)

    response = await settings.run_full_plex_reconcile(MagicMock(), db=mock_db)
    context = cast(dict, getattr(response, "context", None))

    assert context["message_type"] == "success"
    assert context["message"] == "Plex poll completed. Transitioned 2 request(s)."


@pytest.mark.asyncio
async def test_run_full_plex_reconcile_reports_lock_contention(monkeypatch, mock_db, base_context):
    """Manual Plex poll should surface lock contention cleanly."""

    monkeypatch.setattr(
        settings,
        "_build_settings_page_context",
        AsyncMock(return_value=base_context()),
    )
    monkeypatch.setattr(settings, "_build_plex_job_statuses", AsyncMock(return_value=[]))

    scheduler = MagicMock()
    scheduler.trigger_full_plex_reconcile_now = AsyncMock(
        return_value=MagicMock(
            status="locked",
            completed_requests=0,
            error=None,
            lock_owner="worker-1",
            metrics_payload=None,
        )
    )

    import app.siftarr.main as main_module

    monkeypatch.setattr(main_module, "scheduler_service", scheduler)

    response = await settings.run_full_plex_reconcile(MagicMock(), db=mock_db)
    context = cast(dict, getattr(response, "context", None))

    assert context["message_type"] == "error"
    assert context["message"] == "Plex poll is already in progress."
    scheduler.trigger_full_plex_reconcile_now.assert_awaited_once()


@pytest.mark.asyncio
async def test_rescan_plex_route_reports_success(monkeypatch, mock_db, base_context):
    """Manual Plex rescan should report how many requests were completed."""

    monkeypatch.setattr(
        settings,
        "_build_settings_page_context",
        AsyncMock(return_value=base_context()),
    )
    monkeypatch.setattr(
        settings._jobs,
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

    monkeypatch.setattr(settings, "async_session_maker", lambda: FakeWorkerSessionContext())

    created_episode_sync = {}

    class FakeEpisodeSyncService:
        def __init__(self, db, overseerr=None, plex=None):
            created_episode_sync["db"] = db
            created_episode_sync["plex"] = plex

        async def sync_episodes(self, request_id, force_plex_refresh=False):
            assert request_id == 12
            assert force_plex_refresh is True

    import app.siftarr.services.episode_sync_service as episode_sync_module

    monkeypatch.setattr(episode_sync_module, "EpisodeSyncService", FakeEpisodeSyncService)

    polling = AsyncMock()
    polling.get_active_requests = AsyncMock(return_value=[tv_request])
    polling.poll.return_value = 3
    monkeypatch.setattr(settings, "PlexPollingService", lambda db, plex: polling)

    response = await settings.rescan_plex(MagicMock(), db=mock_db)
    context = cast(dict, getattr(response, "context", None))

    assert context["message_type"] == "success"
    assert "Manual Plex rescan completed." in context["message"]
    assert "Re-synced 1 TV request(s)" in context["message"]
    assert "had 0 failed TV request(s)" in context["message"]
    assert "transitioned 3 request(s) to completed" in context["message"]
    assert created_episode_sync["db"] is worker_db
    assert created_episode_sync["plex"] is plex_service
    plex_service.close.assert_awaited_once()
