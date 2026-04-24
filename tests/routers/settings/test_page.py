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
async def test_settings_page_includes_plex_sync_action(monkeypatch, mock_db):
    """Settings page should expose only partial and full primary Plex sync actions."""

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

    assert "Plex Sync" in body
    assert "Partial Sync" in body
    assert "Full Sync" in body
    assert "new or incomplete TV content" in body
    assert "active non-completed TV metadata" in body
    assert "Deep Re-scan" not in body
    assert "Shallow Re-scan" not in body

    plex_row = body[body.index("Plex Sync") : body.index("Reseed Default Rules")]
    assert plex_row.count("<button") == 2
    assert plex_row.count("btn-primary") == 2
    assert "Partial Sync" in plex_row
    assert "Full Sync" in plex_row
    assert "Run Recent Plex Scan" not in plex_row
    assert "Run Plex Poll" not in plex_row


@pytest.mark.asyncio
async def test_settings_page_uses_non_blocking_sync_progress_panels(monkeypatch, mock_db):
    """Overseerr/Plex sync progress should render as dismissible toast panels, not blocking modals."""

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

    assert "sync-toast-region" in body
    assert "overseerr-sync-panel" in body
    assert "plex-sync-panel" in body
    assert "pointer-events-none" in body
    assert "Dismiss Overseerr sync progress" in body
    assert "Dismiss Plex sync progress" in body
    assert "overseerr-sync-modal" not in body
    assert "plex-sync-modal" not in body


@pytest.mark.asyncio
async def test_settings_page_progress_script_clamps_and_handles_unknown_totals(
    monkeypatch, mock_db
):
    """Client progress should use current/total, avoid premature 100%, and support unknown totals."""

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

    assert "const current = Number(data.current ?? data.completed);" in body
    assert "const total = Number(data.total);" in body
    assert "const maximumPercent = final ? 100 : 99;" in body
    assert "Working…" in body
    assert "const values = allValues.slice(0, 5);" in body


@pytest.mark.asyncio
async def test_settings_page_includes_plex_job_status_and_manual_job_actions(
    monkeypatch, mock_db, base_context
):
    """Settings page should show Plex scheduler status and advanced manual triggers."""

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
                        "label": "Recent Plex Scan",
                        "description": "Recent-additions scan for active requests",
                        "last_success": "2026-04-19 12:00:00",
                        "last_run": "2026-04-19 12:05:00",
                        "last_started": "2026-04-19 12:04:00",
                        "locked": False,
                        "lock_owner": None,
                        "last_error": None,
                        "run_summary": "Recent scan completed; completed 2, matched 0, scanned 4",
                        "metrics_snapshot": "completed=2, scanned=4",
                    },
                    {
                        "label": "Plex Poll",
                        "description": "Active-request availability poll",
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

    assert "Advanced / Scheduler" in body
    assert "Plex Scheduler Status" not in body
    assert "Recent Plex Scan" in body
    assert "Plex Poll" in body
    assert "Run Recent Plex Scan" in body
    assert "Run Plex Poll" in body
    assert "Metrics Snapshot" in body
    assert "Last Outcome" in body
    assert "Recent scan completed; completed 2, matched 0, scanned 4" in body
    assert "worker-1" in body

    advanced_index = body.index("Advanced / Scheduler")
    run_recent_index = body.index("Run Recent Plex Scan")
    run_poll_index = body.index("Run Plex Poll")
    metrics_index = body.index("Metrics Snapshot")
    assert advanced_index < run_recent_index < metrics_index
    assert advanced_index < run_poll_index < metrics_index

    advanced_details_start = body.rindex("<details", 0, advanced_index)
    advanced_summary_start = body.rindex("<summary", 0, advanced_index)
    assert advanced_details_start < advanced_summary_start
    assert "open" not in body[advanced_details_start:advanced_summary_start]
