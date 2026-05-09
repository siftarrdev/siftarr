"""Tests for dashboard action routes."""

import json
from typing import cast
from unittest.mock import AsyncMock, MagicMock, call

import pytest
from fastapi import HTTPException

from app.siftarr.models.request import RequestStatus
from app.siftarr.routers import dashboard_actions
from app.siftarr.services.dashboard import search_service as search_service_mod


@pytest.mark.asyncio
async def test_bulk_request_action_redirects_to_requested_tab(mock_db, monkeypatch):
    """Bulk actions should return to the requested tab."""
    request_record = MagicMock()
    request_record.created_at = MagicMock()

    execute_result = MagicMock()
    execute_result.scalars.return_value.all.return_value = [request_record]
    mock_db.execute.return_value = execute_result

    process_request_search = AsyncMock()
    monkeypatch.setattr(
        search_service_mod.SearchService, "process_request_search", process_request_search
    )

    response = await dashboard_actions.bulk_request_action(
        http_request=MagicMock(headers={}),
        action="search",
        request_ids=[1],
        redirect_to="/?tab=active",
        db=mock_db,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/?tab=active"
    process_request_search.assert_awaited_once_with(request_record)


@pytest.mark.asyncio
async def test_bulk_request_action_defaults_to_pending_tab(mock_db):
    """Bulk actions default back to the pending tab."""
    response = await dashboard_actions.bulk_request_action(
        http_request=MagicMock(headers={}),
        action="search",
        request_ids=[],
        redirect_to=None,
        db=mock_db,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/?tab=pending"


@pytest.mark.asyncio
@pytest.mark.parametrize("tab", ["active", "pending"])
async def test_deny_request_json_preserves_redirect_and_denies_record(mock_db, monkeypatch, tab):
    """Single-row deny fetches should return JSON and keep the originating tab."""
    request_record = MagicMock(id=7)
    load_request = AsyncMock(return_value=request_record)
    deny_record = AsyncMock()
    monkeypatch.setattr(dashboard_actions, "load_request_or_404", load_request)
    monkeypatch.setattr(dashboard_actions, "_deny_request_record", deny_record)

    response = await dashboard_actions.deny_request(
        request_id=7,
        http_request=MagicMock(headers={"accept": "application/json"}),
        redirect_to=f"/?tab={tab}",
        reason="Not wanted",
        db=mock_db,
    )

    body = json.loads(cast(bytes, response.body))
    assert body == {
        "status": "ok",
        "message": "Request denied",
        "redirect_to": f"/?tab={tab}",
    }
    load_request.assert_awaited_once_with(mock_db, 7)
    deny_record.assert_awaited_once_with(request_record, mock_db, reason="Not wanted")


@pytest.mark.asyncio
@pytest.mark.parametrize("tab", ["active", "pending"])
async def test_deny_request_redirect_preserves_tab(mock_db, monkeypatch, tab):
    """Non-fetch single deny should still redirect back to the originating tab."""
    request_record = MagicMock(id=8)
    monkeypatch.setattr(
        dashboard_actions, "load_request_or_404", AsyncMock(return_value=request_record)
    )
    deny_record = AsyncMock()
    monkeypatch.setattr(dashboard_actions, "_deny_request_record", deny_record)

    response = await dashboard_actions.deny_request(
        request_id=8,
        http_request=MagicMock(headers={}),
        redirect_to=f"/?tab={tab}",
        reason=None,
        db=mock_db,
    )

    assert response.status_code == 303
    assert response.headers["location"] == f"/?tab={tab}"
    deny_record.assert_awaited_once_with(request_record, mock_db, reason=None)


@pytest.mark.asyncio
@pytest.mark.parametrize("tab", ["active", "pending"])
async def test_bulk_deny_json_preserves_redirect_and_denies_selected(mock_db, monkeypatch, tab):
    """Bulk deny fetches should deny selected rows from both shared tabs."""
    request_a = MagicMock(id=1)
    request_b = MagicMock(id=2)
    execute_result = MagicMock()
    execute_result.scalars.return_value.all.return_value = [request_a, request_b]
    mock_db.execute.return_value = execute_result
    deny_record = AsyncMock()
    monkeypatch.setattr(dashboard_actions, "_deny_request_record", deny_record)

    response = await dashboard_actions.bulk_request_action(
        http_request=MagicMock(headers={"accept": "application/json"}),
        action="deny",
        request_ids=[1, 2],
        redirect_to=f"/?tab={tab}",
        db=mock_db,
    )

    body = json.loads(cast(bytes, response.body))
    assert body == {
        "status": "ok",
        "message": "Denied 2 request(s)",
        "redirect_to": f"/?tab={tab}",
    }
    deny_record.assert_has_awaits(
        [
            call(request_a, mock_db, reason="Bulk denied"),
            call(request_b, mock_db, reason="Bulk denied"),
        ]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("tab", ["active", "pending"])
async def test_bulk_deny_json_no_selected_is_noop(mock_db, tab):
    """Bulk deny with no selected rows should be a JSON no-op for fetch callers."""
    response = await dashboard_actions.bulk_request_action(
        http_request=MagicMock(headers={"accept": "application/json"}),
        action="deny",
        request_ids=[],
        redirect_to=f"/?tab={tab}",
        db=mock_db,
    )

    body = json.loads(cast(bytes, response.body))
    assert body == {
        "status": "ok",
        "message": "No items selected",
        "redirect_to": f"/?tab={tab}",
    }
    mock_db.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_bulk_request_action_searches_all_pending_requests(mock_db, monkeypatch):
    """Search All should load pending/searching requests without selected IDs."""
    pending_request = MagicMock()
    searching_request = MagicMock()

    execute_result = MagicMock()
    execute_result.scalars.return_value.all.return_value = [pending_request, searching_request]
    mock_db.execute.return_value = execute_result

    process_request_search = AsyncMock()
    monkeypatch.setattr(
        search_service_mod.SearchService, "process_request_search", process_request_search
    )

    response = await dashboard_actions.bulk_request_action(
        http_request=MagicMock(headers={}),
        action="search_all_pending",
        request_ids=[],
        redirect_to="/?tab=pending",
        db=mock_db,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/?tab=pending"
    process_request_search.assert_has_awaits([call(pending_request), call(searching_request)])


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
async def test_deny_request_record_declines_overseerr_removes_queue_and_transitions(
    mock_db, monkeypatch
):
    """Denying any request should notify Overseerr, clear pending state, and transition denied."""
    request_record = MagicMock(id=42, overseerr_request_id=1234)
    overseerr_instance = MagicMock()
    overseerr_instance.decline_request = AsyncMock()
    queue_instance = MagicMock()
    queue_instance.remove_from_queue = AsyncMock()
    lifecycle_instance = MagicMock()
    lifecycle_instance.transition = AsyncMock()

    monkeypatch.setattr(dashboard_actions, "OverseerrService", lambda settings: overseerr_instance)
    monkeypatch.setattr(dashboard_actions, "PendingQueueService", lambda db: queue_instance)
    monkeypatch.setattr(dashboard_actions, "LifecycleService", lambda db: lifecycle_instance)

    await dashboard_actions._deny_request_record(request_record, mock_db, reason="No thanks")

    overseerr_instance.decline_request.assert_awaited_once_with(1234, reason="No thanks")
    queue_instance.remove_from_queue.assert_awaited_once_with(42)
    lifecycle_instance.transition.assert_awaited_once_with(
        42, RequestStatus.DENIED, reason="No thanks"
    )


@pytest.mark.asyncio
async def test_deny_request_record_skips_overseerr_when_not_linked(mock_db, monkeypatch):
    """Local-only denied transitions should still clear queue and transition state."""
    request_record = MagicMock(id=43, overseerr_request_id=None)
    overseerr_instance = MagicMock()
    overseerr_instance.decline_request = AsyncMock()
    queue_instance = MagicMock()
    queue_instance.remove_from_queue = AsyncMock()
    lifecycle_instance = MagicMock()
    lifecycle_instance.transition = AsyncMock()

    monkeypatch.setattr(dashboard_actions, "OverseerrService", lambda settings: overseerr_instance)
    monkeypatch.setattr(dashboard_actions, "PendingQueueService", lambda db: queue_instance)
    monkeypatch.setattr(dashboard_actions, "LifecycleService", lambda db: lifecycle_instance)

    await dashboard_actions._deny_request_record(request_record, mock_db, reason=None)

    overseerr_instance.decline_request.assert_not_awaited()
    queue_instance.remove_from_queue.assert_awaited_once_with(43)
    lifecycle_instance.transition.assert_awaited_once_with(43, RequestStatus.DENIED, reason=None)


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

    mock_staging_instance = AsyncMock()
    mock_staging_instance.use_releases = AsyncMock(return_value={"status": "staged"})
    monkeypatch.setattr(dashboard_actions, "StagingService", lambda db: mock_staging_instance)

    response = await dashboard_actions.use_request_release(
        request_id=21,
        release_id=99,
        http_request=MagicMock(headers={}),
        redirect_to=None,
        db=mock_db,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/?tab=staged"
    mock_staging_instance.use_releases.assert_awaited_once_with(
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
    staging_instance = AsyncMock()
    staging_instance.use_releases = AsyncMock(return_value={"status": "staged"})

    monkeypatch.setattr(
        search_service_mod.RuleEngine,
        "from_db_rules",
        MagicMock(return_value=fake_engine),
    )
    monkeypatch.setattr(search_service_mod, "persist_manual_release", persist_manual_release)
    monkeypatch.setattr(search_service_mod, "StagingService", lambda db: staging_instance)

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
        uploaded_by=None,
        redirect_to=None,
        db=mock_db,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/?tab=staged"
    persist_manual_release.assert_awaited_once()
    staging_instance.use_releases.assert_awaited_once_with(
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

    staging_instance = AsyncMock()
    staging_instance.use_releases = AsyncMock(
        return_value={"status": "staged", "action": "auto_staged"}
    )
    monkeypatch.setattr(dashboard_actions, "StagingService", lambda db: staging_instance)

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
    staging_instance = AsyncMock()
    staging_instance.use_releases = AsyncMock(
        return_value={"status": "staged", "action": "replaced_active_selection"}
    )

    monkeypatch.setattr(
        search_service_mod.RuleEngine,
        "from_db_rules",
        MagicMock(return_value=fake_engine),
    )
    monkeypatch.setattr(search_service_mod, "persist_manual_release", persist_manual_release)
    monkeypatch.setattr(search_service_mod, "StagingService", lambda db: staging_instance)

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
        uploaded_by=None,
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
