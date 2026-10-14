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
    assert 'name="overseerr_poll_interval_minutes" value="5"' in body
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
