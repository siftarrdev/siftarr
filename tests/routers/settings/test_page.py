"""Settings page rendering tests."""

from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.siftarr.routers import settings


@pytest.mark.asyncio
async def test_get_settings_page_includes_clear_cache_scope_copy(monkeypatch, mock_db):
    """Settings page should describe the app-side cache-clearing scope and limits."""

    rule_service = MagicMock()
    rule_service.ensure_default_rules = AsyncMock()

    monkeypatch.setattr(settings, "RuleService", lambda db: rule_service)
    monkeypatch.setattr(
        settings,
        "_build_settings_page_context",
        AsyncMock(
            return_value={
                "request": MagicMock(),
                "env": {
                    "overseerr_url": "",
                    "overseerr_api_key": "",
                    "prowlarr_url": "",
                    "prowlarr_api_key": "",
                    "qbittorrent_url": "",
                    "qbittorrent_username": "",
                    "qbittorrent_password": "",
                    "plex_url": "",
                    "plex_token": "",
                    "tz": "UTC",
                },
                "staging_enabled": True,
                "pending_count": 0,
                "stats": {"total_requests": 0, "completed": 0, "pending": 0, "failed": 0},
            }
        ),
    )

    response = await settings.get_settings_page(MagicMock(), db=mock_db)
    body = cast(bytes, response.body).decode()

    assert "Clear App Search Cache" in body
    assert "stored releases" in body
    assert "Overseerr status cache" in body


@pytest.mark.asyncio
async def test_settings_page_includes_reseed_default_snapshot_copy(monkeypatch, mock_db):
    """Settings copy should describe reseeding the checked-in 12-rule snapshot."""

    rule_service = MagicMock()
    rule_service.ensure_default_rules = AsyncMock()

    monkeypatch.setattr(settings, "RuleService", lambda db: rule_service)
    monkeypatch.setattr(
        settings,
        "_build_settings_page_context",
        AsyncMock(
            return_value={
                "request": MagicMock(),
                "env": {
                    "overseerr_url": "",
                    "overseerr_api_key": "",
                    "prowlarr_url": "",
                    "prowlarr_api_key": "",
                    "qbittorrent_url": "",
                    "qbittorrent_username": "",
                    "qbittorrent_password": "",
                    "plex_url": "",
                    "plex_token": "",
                    "tz": "UTC",
                },
                "staging_enabled": True,
                "pending_count": 0,
                "stats": {"total_requests": 0, "completed": 0, "pending": 0, "failed": 0},
            }
        ),
    )

    response = await settings.get_settings_page(MagicMock(), db=mock_db)
    body = cast(bytes, response.body).decode()

    assert "checked-in 12-rule default snapshot" in body


@pytest.mark.asyncio
async def test_settings_page_includes_rescan_plex_action(monkeypatch, mock_db):
    """Settings page should expose the Plex availability rescan action."""

    rule_service = MagicMock()
    rule_service.ensure_default_rules = AsyncMock()

    monkeypatch.setattr(settings, "RuleService", lambda db: rule_service)
    monkeypatch.setattr(
        settings,
        "_build_settings_page_context",
        AsyncMock(
            return_value={
                "request": MagicMock(),
                "env": {
                    "overseerr_url": "",
                    "overseerr_api_key": "",
                    "prowlarr_url": "",
                    "prowlarr_api_key": "",
                    "qbittorrent_url": "",
                    "qbittorrent_username": "",
                    "qbittorrent_password": "",
                    "plex_url": "",
                    "plex_token": "",
                    "tz": "UTC",
                },
                "staging_enabled": True,
                "pending_count": 0,
                "stats": {"total_requests": 0, "completed": 0, "pending": 0, "failed": 0},
            }
        ),
    )

    response = await settings.get_settings_page(MagicMock(), db=mock_db)
    body = cast(bytes, response.body).decode()

    assert "Re-scan Plex Availability" in body
    assert "Re-scan Plex" in body


@pytest.mark.asyncio
async def test_settings_page_includes_plex_job_status_and_manual_job_actions(
    monkeypatch, mock_db, base_context
):
    """Settings page should show split Plex job status and manual trigger actions."""

    rule_service = MagicMock()
    rule_service.ensure_default_rules = AsyncMock()

    monkeypatch.setattr(settings, "RuleService", lambda db: rule_service)
    monkeypatch.setattr(
        settings,
        "_build_settings_page_context",
        AsyncMock(
            return_value={
                **base_context(),
                "plex_jobs": [
                    {
                        "label": "Incremental Plex Sync",
                        "description": "Fast recent-added availability scan",
                        "last_success": "2026-04-19 12:00:00",
                        "last_run": "2026-04-19 12:05:00",
                        "last_started": "2026-04-19 12:04:00",
                        "locked": False,
                        "lock_owner": None,
                        "last_error": None,
                        "run_summary": "Incremental run completed",
                        "metrics_snapshot": "completed=2, scanned=4",
                    },
                    {
                        "label": "Full Plex Reconcile",
                        "description": "Slower full-library reconciliation run",
                        "last_success": None,
                        "last_run": None,
                        "last_started": None,
                        "locked": True,
                        "lock_owner": "worker-1",
                        "last_error": "plex timeout",
                        "run_summary": "Skipped due to lock (worker-1)",
                        "metrics_snapshot": "completed=0, scanned=0",
                    },
                ],
            }
        ),
    )

    response = await settings.get_settings_page(MagicMock(), db=mock_db)
    body = cast(bytes, response.body).decode()

    assert "Plex Scheduler Status" in body
    assert "Incremental Plex Sync" in body
    assert "Full Plex Reconcile" in body
    assert "Run Incremental Plex Sync" in body
    assert "Run Full Plex Reconcile" in body
    assert "Metrics Snapshot" in body
    assert "Last Outcome" in body
    assert "Incremental run completed" in body
    assert "worker-1" in body
