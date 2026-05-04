"""Tests for dashboard action routes."""

import json
from typing import cast
from unittest.mock import AsyncMock, MagicMock, call

import pytest
from fastapi import HTTPException

from app.siftarr.models.request import RequestStatus
from app.siftarr.routers import dashboard_actions


@pytest.mark.asyncio
async def test_bulk_request_action_redirects_to_requested_tab(mock_db, monkeypatch):
    """Bulk actions should return to the requested tab."""
    request_record = MagicMock()
    request_record.created_at = MagicMock()

    execute_result = MagicMock()
    execute_result.scalars.return_value.all.return_value = [request_record]
    mock_db.execute.return_value = execute_result

    process_request_search = AsyncMock()
    monkeypatch.setattr(dashboard_actions, "_process_request_search", process_request_search)

    response = await dashboard_actions.bulk_request_action(
        action="search",
        request_ids=[1],
        redirect_to="/?tab=active",
        db=mock_db,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/?tab=active"
    process_request_search.assert_awaited_once_with(request_record, mock_db)


@pytest.mark.asyncio
async def test_bulk_request_action_defaults_to_pending_tab(mock_db):
    """Bulk actions default back to the pending tab."""
    response = await dashboard_actions.bulk_request_action(
        action="search",
        request_ids=[],
        redirect_to=None,
        db=mock_db,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/?tab=pending"


@pytest.mark.asyncio
async def test_bulk_request_action_searches_all_pending_requests(mock_db, monkeypatch):
    """Search All should load pending/searching requests without selected IDs."""
    pending_request = MagicMock()
    searching_request = MagicMock()

    execute_result = MagicMock()
    execute_result.scalars.return_value.all.return_value = [pending_request, searching_request]
    mock_db.execute.return_value = execute_result

    process_request_search = AsyncMock()
    monkeypatch.setattr(dashboard_actions, "_process_request_search", process_request_search)

    response = await dashboard_actions.bulk_request_action(
        action="search_all_pending",
        request_ids=[],
        redirect_to="/?tab=pending",
        db=mock_db,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/?tab=pending"
    process_request_search.assert_has_awaits(
        [call(pending_request, mock_db), call(searching_request, mock_db)]
    )


@pytest.mark.asyncio
async def test_deny_request_success():
    """Deny helper should surface successful declines."""
    mock_overseerr_service = AsyncMock()
    mock_overseerr_service.decline_request.return_value = True

    result = await mock_overseerr_service.decline_request(123)

    assert result is True
    mock_overseerr_service.decline_request.assert_called_once_with(123)


@pytest.mark.asyncio
async def test_deny_request_not_found():
    """Deny helper should map a missing request to 404."""
    mock_overseerr_service = AsyncMock()
    mock_overseerr_service.decline_request.return_value = False

    from starlette.exceptions import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        if not await mock_overseerr_service.decline_request(999):
            raise HTTPException(status_code=404, detail="Request not found")

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_use_request_release_redirects_pending_requests_to_pending_tab(mock_db, monkeypatch):
    """Stored release selection should redirect to staged view to highlight the active pick."""
    request_record = MagicMock()
    request_record.id = 21
    request_record.status = RequestStatus.PENDING
    release_record = MagicMock(id=99)

    request_result = MagicMock()
    request_result.scalar_one_or_none.return_value = request_record
    release_result = MagicMock()
    release_result.scalar_one_or_none.return_value = release_record
    mock_db.execute.side_effect = [request_result, release_result]

    use_releases = AsyncMock(return_value={"status": "staged"})
    monkeypatch.setattr(dashboard_actions, "use_releases", use_releases)

    response = await dashboard_actions.use_request_release(
        request_id=21,
        release_id=99,
        http_request=MagicMock(headers={}),
        redirect_to=None,
        db=mock_db,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/?tab=staged"
    use_releases.assert_awaited_once_with(
        mock_db,
        request_record,
        [release_record],
        selection_source="manual",
    )


@pytest.mark.asyncio
async def test_use_manual_release_persists_then_uses_release(mock_db, monkeypatch):
    """Ad hoc manual-search releases should persist then use the normal release flow."""
    request_record = MagicMock()
    request_record.id = 21
    request_record.status = RequestStatus.PENDING

    request_result = MagicMock()
    request_result.scalar_one_or_none.return_value = request_record
    rules_result = MagicMock()
    rules_result.scalars.return_value.all.return_value = []
    mock_db.execute.side_effect = [request_result, rules_result]

    stored_release = MagicMock(id=123)
    fake_engine = MagicMock()
    fake_engine.evaluate.return_value = MagicMock(total_score=55, passed=True, matches=[])
    persist_manual_release = AsyncMock(return_value=stored_release)
    use_releases = AsyncMock(return_value={"status": "staged"})

    monkeypatch.setattr(
        dashboard_actions.RuleEngine,
        "from_db_rules",
        MagicMock(return_value=fake_engine),
    )
    monkeypatch.setattr(dashboard_actions, "persist_manual_release", persist_manual_release)
    monkeypatch.setattr(dashboard_actions, "use_releases", use_releases)

    response = await dashboard_actions.use_manual_release(
        request_id=21,
        http_request=MagicMock(headers={}),
        title="Foundation.S01E01.1080p.WEB-DL",
        size=2,
        seeders=10,
        leechers=1,
        indexer="IndexerA",
        download_url="https://example.test/foundation.torrent",
        magnet_url=None,
        info_hash="abc123",
        publish_date="2026-04-16T00:00:00+00:00",
        resolution="1080p",
        codec="x265",
        release_group="GROUP",
        redirect_to=None,
        db=mock_db,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/?tab=staged"
    persist_manual_release.assert_awaited_once()
    use_releases.assert_awaited_once_with(
        mock_db,
        request_record,
        [stored_release],
        selection_source="manual",
    )


@pytest.mark.asyncio
async def test_use_request_release_json_reports_auto_stage_outcome(mock_db, monkeypatch):
    """JSON release selection responses should clearly call out auto-staging."""
    request_record = MagicMock()
    request_record.id = 21
    request_record.status = RequestStatus.PENDING
    release_record = MagicMock(id=99)

    request_result = MagicMock()
    request_result.scalar_one_or_none.return_value = request_record
    release_result = MagicMock()
    release_result.scalar_one_or_none.return_value = release_record
    mock_db.execute.side_effect = [request_result, release_result]

    use_releases = AsyncMock(return_value={"status": "staged", "action": "auto_staged"})
    monkeypatch.setattr(dashboard_actions, "use_releases", use_releases)

    response = await dashboard_actions.use_request_release(
        request_id=21,
        release_id=99,
        http_request=MagicMock(headers={"accept": "application/json"}),
        redirect_to=None,
        db=mock_db,
    )

    body = json.loads(cast(bytes, response.body))
    assert body == {"status": "ok", "message": "Request auto-staged successfully"}


@pytest.mark.asyncio
async def test_use_manual_release_json_reports_replacement_outcome(mock_db, monkeypatch):
    """JSON manual selection responses should clearly call out replacements."""
    request_record = MagicMock()
    request_record.id = 21
    request_record.status = RequestStatus.STAGED

    request_result = MagicMock()
    request_result.scalar_one_or_none.return_value = request_record
    rules_result = MagicMock()
    rules_result.scalars.return_value.all.return_value = []
    mock_db.execute.side_effect = [request_result, rules_result]

    stored_release = MagicMock(id=123)
    fake_engine = MagicMock()
    fake_engine.evaluate.return_value = MagicMock(total_score=55, passed=True, matches=[])
    persist_manual_release = AsyncMock(return_value=stored_release)
    use_releases = AsyncMock(
        return_value={"status": "staged", "action": "replaced_active_selection"}
    )

    monkeypatch.setattr(
        dashboard_actions.RuleEngine,
        "from_db_rules",
        MagicMock(return_value=fake_engine),
    )
    monkeypatch.setattr(dashboard_actions, "persist_manual_release", persist_manual_release)
    monkeypatch.setattr(dashboard_actions, "use_releases", use_releases)

    response = await dashboard_actions.use_manual_release(
        request_id=21,
        http_request=MagicMock(headers={"accept": "application/json"}),
        title="Foundation.S01E01.1080p.WEB-DL",
        size=2,
        seeders=10,
        leechers=1,
        indexer="IndexerA",
        download_url="https://example.test/foundation.torrent",
        magnet_url=None,
        info_hash="abc123",
        publish_date="2026-04-16T00:00:00+00:00",
        resolution="1080p",
        codec="x265",
        release_group="GROUP",
        redirect_to=None,
        db=mock_db,
    )

    body = json.loads(cast(bytes, response.body))
    assert body == {"status": "ok", "message": "Active staged selection replaced successfully"}


@pytest.mark.asyncio
async def test_use_manual_release_rejects_invalid_publish_date(mock_db):
    """Manual selection should fail fast on invalid publish dates."""
    request_record = MagicMock()
    request_record.id = 21
    request_record.status = RequestStatus.PENDING

    request_result = MagicMock()
    request_result.scalar_one_or_none.return_value = request_record
    mock_db.execute.return_value = request_result

    with pytest.raises(HTTPException) as exc_info:
        await dashboard_actions.use_manual_release(
            request_id=21,
            http_request=MagicMock(headers={}),
            title="Foundation.S01E01.1080p.WEB-DL",
            size=2,
            seeders=10,
            leechers=1,
            indexer="IndexerA",
            download_url="https://example.test/foundation.torrent",
            magnet_url=None,
            info_hash=None,
            publish_date="not-a-date",
            resolution=None,
            codec=None,
            release_group=None,
            redirect_to=None,
            db=mock_db,
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Invalid publish_date"
