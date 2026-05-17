"""Settings page rendering tests."""

from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.siftarr.routers import settings


async def _render_settings_page(monkeypatch, mock_db, context):
    rule_service = MagicMock()
    rule_service.ensure_default_rules = AsyncMock()
    monkeypatch.setattr(settings, "RuleService", lambda db: rule_service)
    monkeypatch.setattr(
        settings,
        "_build_settings_page_context",
        AsyncMock(return_value=context),
    )
    response = await settings.get_settings_page(MagicMock(), db=mock_db)
    return cast(bytes, response.body).decode()


@pytest.mark.asyncio
async def test_settings_page_layout_defaults_and_connections(monkeypatch, mock_db, base_context):
    """Settings page should prioritize manual actions and collapse other sections."""

    body = await _render_settings_page(monkeypatch, mock_db, base_context())

    assert body.count('<details open class="card">') == 1
    assert body.index("Manual Actions") < body.index("Connection Settings")

    connection_start = body.index("Connection Settings")
    connection_details_start = body.rindex("<details", 0, connection_start)
    connection_summary_start = body.rindex("<summary", 0, connection_start)
    assert "open" not in body[connection_details_start:connection_summary_start]

    connection_end = body.index("Advanced / Scheduler")
    connection_section = body[connection_start:connection_end]
    assert connection_section.index("Plex") < connection_section.index("Overseerr")
    assert connection_section.index("Overseerr") < connection_section.index("Prowlarr")
    assert connection_section.index("Prowlarr") < connection_section.index("qBittorrent")
    assert "For scripts, webhooks, and direct API clients" in connection_section
    assert "X-API-Key" in connection_section

    assert "Database Statistics" not in body
    assert "Total Requests" not in body
    assert "Staging Mode" in body
    assert "qBittorrent Move / Retention" in body
    assert 'name="qbittorrent_move_completed_dir" value="/downloads"' in body
    assert 'name="qbittorrent_move_retention_weeks" value="6"' in body
    assert "Scheduler Settings" in body
    assert "Settings Backup / Restore" in body
    assert "Background Jobs" in body
    assert "API keys, Plex tokens, generated secrets" in body
    assert 'name="overseerr_poll_interval_minutes" value="60"' in body
    assert 'name="qbittorrent_completion_poll_interval_seconds" value="30"' in body
    assert 'name="plex_fast_sync_interval_minutes" value="5"' in body
    assert 'name="plex_full_sync_time" value="03:00"' in body


@pytest.mark.asyncio
async def test_settings_page_renders_job_rows_without_secret_values(
    monkeypatch, mock_db, base_context
):
    context = base_context()
    context["scheduler_status"] = {
        "available": True,
        "running": True,
        "active_jobs": [{"id": "plex_poll", "lock_owner": "worker"}],
        "jobs": [
            {
                "id": "plex_poll",
                "label": "Plex Poll",
                "next_run": "2026-01-02 03:00:00+00:00",
                "last_run": "2026-01-01 03:00:00+00:00",
                "last_success": "2026-01-01 03:00:00+00:00",
                "last_error": None,
                "locked": True,
                "manual_action": "/settings/run-plex-poll",
            }
        ],
    }
    context["env"]["overseerr_api_key"] = "********cret"

    body = await _render_settings_page(monkeypatch, mock_db, context)

    assert "Scheduler: <span" in body
    assert "Plex Poll" in body
    assert "2026-01-02 03:00:00+00:00" in body
    assert "/settings/run-plex-poll" in body
    assert "real-secret" not in body


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
                    "qbittorrent_api_key": "",
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
                    "qbittorrent_api_key": "",
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
                    "qbittorrent_api_key": "",
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
    assert "recently added Plex items" in body
    assert "active TV metadata" in body
    assert "Deep Re-scan" not in body
    assert "Shallow Re-scan" not in body

    plex_row = body[body.index("Plex Sync") : body.index("qBittorrent Move / Retention")]
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
                    "qbittorrent_api_key": "",
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
                    "qbittorrent_api_key": "",
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

    assert "const serverPercent = Number(data.overall_percent ?? data.progress_percent);" in body
    assert "const current = Number(data.overall_current ?? data.current ?? data.completed);" in body
    assert "const total = Number(data.overall_total ?? data.total);" in body
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
    assert "Recent Plex Scan" not in body
    assert "Plex Poll" not in body
    assert "Run Recent Plex Scan" not in body
    assert "Run Plex Poll" not in body
    assert "Metrics Snapshot" not in body
    assert "Last Outcome" not in body
    assert "Recent scan completed; completed 2, matched 0, scanned 4" not in body
    assert "worker-1" not in body

    advanced_index = body.index("Advanced / Scheduler")

    advanced_details_start = body.rindex("<details", 0, advanced_index)
    advanced_summary_start = body.rindex("<summary", 0, advanced_index)
    assert advanced_details_start < advanced_summary_start
    assert "open" not in body[advanced_details_start:advanced_summary_start]
