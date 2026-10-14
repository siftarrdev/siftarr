"""Tests for dashboard action routes."""

import json
from datetime import UTC, datetime
from typing import cast
from unittest.mock import AsyncMock, MagicMock, call

import pytest
from fastapi import HTTPException
from sqlalchemy.dialects import sqlite

from app.siftarr.models.request import MediaType, RequestStatus
from app.siftarr.routers import dashboard_actions
from app.siftarr.services.dashboard import search_service as search_service_mod
from app.siftarr.services.integrations.prowlarr_service import ProwlarrRelease
from app.siftarr.services.lifecycle.episode_derive import (
    derive_request_status_from_episodes,
    derive_season_status,
)
from tests.factories import make_episode, make_request, make_season

S = RequestStatus

_real_require_release_confirmations = dashboard_actions._require_release_confirmations


@pytest.fixture(autouse=True)
def _linked_overseerr_ids_from_primary(monkeypatch):
    """Resolve linked Seerr IDs from the mock request's primary ID without DB queries."""

    async def fake_linked_ids(_db, request):
        primary = request.overseerr_request_id
        return [primary] if isinstance(primary, int) else []

    monkeypatch.setattr(dashboard_actions, "get_linked_overseerr_request_ids", fake_linked_ids)


@pytest.fixture(autouse=True)
def _stable_release_preflight(monkeypatch):
    """Keep legacy route-shape tests focused on their original concern."""
    evaluation = MagicMock(passed=True, total_score=0, rejection_reason=None, matches=[])
    engine = MagicMock()
    engine.evaluate.return_value = evaluation
    monkeypatch.setattr(dashboard_actions, "get_rule_engine", AsyncMock(return_value=engine))
    monkeypatch.setattr(
        dashboard_actions.ReleaseDispositionService,
        "blocked",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        dashboard_actions.ReleaseDispositionService,
        "record_rejection",
        AsyncMock(),
    )
    monkeypatch.setattr(dashboard_actions, "_require_release_confirmations", AsyncMock())
    monkeypatch.setattr(dashboard_actions, "build_prowlarr_release", MagicMock())


@pytest.mark.asyncio
async def test_episode_selection_uses_current_scores_and_skips_rejected_alternatives(
    mock_db, monkeypatch
):
    request = MagicMock(id=7, media_type=MediaType.TV)
    monkeypatch.setattr(dashboard_actions, "load_request_or_404", AsyncMock(return_value=request))

    def stored(release_id, episode, title, old_score, seeders):
        item = MagicMock()
        item.id, item.episode_number, item.title = release_id, episode, title
        item.score, item.seeders = old_score, seeders
        return item

    stale_top = stored(1, 1, "stale", 100, 20)
    current_top = stored(2, 1, "current", 0, 5)
    rejected = stored(3, 1, "rejected", 500, 50)
    episodes_result = MagicMock()
    episodes_result.scalars.return_value.all.return_value = [1]
    releases_result = MagicMock()
    releases_result.scalars.return_value.all.return_value = [stale_top, current_top, rejected]
    mock_db.execute.side_effect = [episodes_result, releases_result]

    monkeypatch.setattr(
        dashboard_actions,
        "build_prowlarr_release",
        lambda item: ProwlarrRelease(
            title=item.title,
            size=1,
            seeders=item.seeders,
            leechers=0,
            download_url="https://example.test/torrent",
            indexer="test",
        ),
    )
    engine = MagicMock()
    engine.evaluate.side_effect = lambda candidate, **_: MagicMock(
        passed=True,
        total_score={"stale": 1, "current": 50, "rejected": 100}[candidate.title],
        rejection_reason=None,
        matches=[],
    )
    monkeypatch.setattr(dashboard_actions, "get_rule_engine", AsyncMock(return_value=engine))
    blocked = AsyncMock(
        side_effect=lambda _request_id, item, **_: MagicMock() if item is rejected else None
    )
    monkeypatch.setattr(dashboard_actions.ReleaseDispositionService, "blocked", blocked)
    preflight = AsyncMock()
    monkeypatch.setattr(dashboard_actions, "_require_release_confirmations", preflight)
    staging = AsyncMock()
    staging.stage_individual_episode_releases.return_value = {"status": "staged"}
    monkeypatch.setattr(dashboard_actions, "StagingService", lambda _db: staging)

    await dashboard_actions.stage_individual_episode_releases(
        7, 1, MagicMock(headers={"accept": "application/json"}), redirect_to=None, db=mock_db
    )

    staging.stage_individual_episode_releases.assert_awaited_once_with(request, 1, [current_top])
    assert preflight.await_args_list[0].args[2] == [current_top]


@pytest.mark.asyncio
async def test_confirmation_conflict_does_not_update_stored_score(mock_db, monkeypatch):
    request = MagicMock(id=7, media_type=MediaType.TV)
    release = MagicMock(id=9, score=77, rule_evidence=None, created_at=datetime.now(UTC))
    release.seeders = 10
    candidate = ProwlarrRelease(
        title="Show.S01E01",
        size=1,
        seeders=10,
        leechers=0,
        download_url="https://example.test/torrent",
        indexer="test",
    )
    monkeypatch.setattr(dashboard_actions, "build_prowlarr_release", lambda _: candidate)
    monkeypatch.setattr(dashboard_actions, "snapshot_warnings", lambda _: [])
    monkeypatch.setattr(dashboard_actions, "identity_warning", lambda *_: None)
    evaluation = MagicMock(
        passed=False, total_score=-50, rejection_reason="current rule failure", matches=[]
    )
    engine = MagicMock()
    engine.evaluate.return_value = evaluation
    monkeypatch.setattr(dashboard_actions, "get_rule_engine", AsyncMock(return_value=engine))

    with pytest.raises(HTTPException) as exc:
        await _real_require_release_confirmations(mock_db, request, [release], {})

    assert exc.value.status_code == 409
    assert release.score == 77


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("redirect_to", "request_ids", "location"),
    [
        ("/?tab=active", [1], "/?tab=active"),
        (None, [], "/?tab=pending"),
    ],
)
async def test_bulk_request_action_redirects_to_requested_tab_or_pending(
    mock_db, monkeypatch, redirect_to, request_ids, location
):
    request_record = MagicMock()
    request_record.created_at = MagicMock()
    execute_result = MagicMock()
    execute_result.scalars.return_value.all.return_value = [request_record] if request_ids else []
    mock_db.execute.return_value = execute_result
    process_request_search = AsyncMock()
    monkeypatch.setattr(
        search_service_mod.SearchService, "process_request_search", process_request_search
    )

    response = await dashboard_actions.bulk_request_action(
        http_request=MagicMock(headers={}),
        action="search",
        request_ids=request_ids,
        redirect_to=redirect_to,
        db=mock_db,
    )

    assert response.status_code == 303
    assert response.headers["location"] == location
    if request_ids:
        process_request_search.assert_awaited_once_with(request_record)


@pytest.mark.asyncio
@pytest.mark.parametrize("tab", ["active", "pending"])
async def test_deny_actions_return_to_the_originating_tab(mock_db, monkeypatch, tab):
    """Single deny (fetch and form) and bulk deny keep the originating tab."""
    request_a = MagicMock(id=7)
    request_b = MagicMock(id=8)
    load_request = AsyncMock(return_value=request_a)
    deny_record = AsyncMock()
    monkeypatch.setattr(dashboard_actions, "load_request_or_404", load_request)
    monkeypatch.setattr(dashboard_actions, "_deny_request_record", deny_record)
    redirect_to = f"/?tab={tab}"

    fetched = await dashboard_actions.deny_request(
        request_id=7,
        http_request=MagicMock(headers={"accept": "application/json"}),
        redirect_to=redirect_to,
        reason="Not wanted",
        db=mock_db,
    )
    form = await dashboard_actions.deny_request(
        request_id=7,
        http_request=MagicMock(headers={}),
        redirect_to=redirect_to,
        reason=None,
        db=mock_db,
    )
    execute_result = MagicMock()
    execute_result.scalars.return_value.all.return_value = [request_a, request_b]
    mock_db.execute.return_value = execute_result
    bulk = await dashboard_actions.bulk_request_action(
        http_request=MagicMock(headers={"accept": "application/json"}),
        action="deny",
        request_ids=[7, 8],
        redirect_to=redirect_to,
        db=mock_db,
    )

    assert json.loads(cast(bytes, fetched.body)) == {
        "status": "ok",
        "message": "Request denied",
        "redirect_to": redirect_to,
    }
    assert form.status_code == 303
    assert form.headers["location"] == redirect_to
    assert json.loads(cast(bytes, bulk.body)) == {
        "status": "ok",
        "message": "Denied 2 request(s)",
        "redirect_to": redirect_to,
    }
    deny_record.assert_has_awaits(
        [
            call(request_a, mock_db, reason="Not wanted"),
            call(request_a, mock_db, reason=None),
            call(request_a, mock_db, reason="Bulk denied"),
            call(request_b, mock_db, reason="Bulk denied"),
        ]
    )


@pytest.mark.asyncio
async def test_bulk_deny_json_no_selected_is_noop(mock_db):
    """Bulk deny with no selected rows should be a JSON no-op for fetch callers."""
    response = await dashboard_actions.bulk_request_action(
        http_request=MagicMock(headers={"accept": "application/json"}),
        action="deny",
        request_ids=[],
        redirect_to="/?tab=pending",
        db=mock_db,
    )

    assert json.loads(cast(bytes, response.body)) == {
        "status": "ok",
        "message": "No items selected",
        "redirect_to": "/?tab=pending",
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
@pytest.mark.parametrize(
    ("overseerr_id", "decline"),
    [
        (1234, AsyncMock()),
        # Missing or stale Overseerr links must not block local cleanup.
        (1235, AsyncMock(return_value=False)),
        (1236, AsyncMock(side_effect=RuntimeError("not found"))),
        (None, AsyncMock()),
    ],
    ids=["declined", "decline-returns-false", "decline-raises", "not-linked"],
)
async def test_deny_request_record_always_clears_queue_and_transitions_to_denied(
    mock_db, monkeypatch, overseerr_id, decline
):
    request_record = MagicMock(id=42, overseerr_request_id=overseerr_id)
    overseerr_instance = MagicMock(decline_request=decline)
    queue_instance = MagicMock()
    queue_instance.remove_from_queue = AsyncMock()
    lifecycle_instance = MagicMock()
    lifecycle_instance.transition = AsyncMock()
    monkeypatch.setattr(dashboard_actions, "OverseerrService", lambda settings: overseerr_instance)
    monkeypatch.setattr(dashboard_actions, "PendingQueueService", lambda db: queue_instance)
    monkeypatch.setattr(dashboard_actions, "LifecycleService", lambda db: lifecycle_instance)

    await dashboard_actions._deny_request_record(request_record, mock_db, reason="No thanks")

    if overseerr_id is None:
        decline.assert_not_awaited()
    else:
        decline.assert_awaited_once_with(overseerr_id, reason="No thanks")
    queue_instance.remove_from_queue.assert_awaited_once_with(42)
    lifecycle_instance.transition.assert_awaited_once_with(
        42, RequestStatus.DENIED, reason="No thanks"
    )


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
        identity_override=False,
        rejection_override=False,
    )


@pytest.mark.asyncio
async def test_use_request_release_approve_now_bypasses_staging(mock_db, monkeypatch):
    request_record = MagicMock(id=21, status=RequestStatus.PENDING)
    release_record = MagicMock(id=99)
    request_result = MagicMock()
    request_result.scalar_one_or_none.return_value = request_record
    release_result = MagicMock()
    release_result.scalar_one_or_none.return_value = release_record
    mock_db.execute.side_effect = [request_result, release_result]

    staging_instance = AsyncMock()
    staging_instance.use_releases = AsyncMock(return_value={"status": "downloading"})
    monkeypatch.setattr(dashboard_actions, "StagingService", lambda db: staging_instance)

    await dashboard_actions.use_request_release(
        request_id=21,
        release_id=99,
        http_request=MagicMock(headers={"accept": "application/json"}),
        redirect_to=None,
        approve_now=True,
        db=mock_db,
    )

    staging_instance.use_releases.assert_awaited_once_with(
        request_record,
        [release_record],
        selection_source="manual",
        force_download=True,
        identity_override=False,
        rejection_override=False,
    )


@pytest.mark.asyncio
async def test_reject_request_release_marks_candidate_rejected(mock_db):
    request_record = MagicMock(id=21)
    release_record = MagicMock(id=99, passed_rules=True, rejection_reason=None)
    request_result = MagicMock()
    request_result.scalar_one_or_none.return_value = request_record
    release_result = MagicMock()
    release_result.scalar_one_or_none.return_value = release_record
    mock_db.execute.side_effect = [request_result, release_result]

    response = await dashboard_actions.reject_request_release(21, 99, db=mock_db)

    assert response.status_code == 200
    assert release_record.passed_rules is False
    assert release_record.rejection_reason == "Manually rejected"
    mock_db.commit.assert_awaited_once()


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
    monkeypatch.setattr(
        dashboard_actions, "_persist_manual_candidate_uncommitted", persist_manual_release
    )
    monkeypatch.setattr(dashboard_actions, "StagingService", lambda db: staging_instance)

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
        force_download=False,
        identity_override=False,
        rejection_override=False,
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
async def test_stage_individual_episode_releases_uses_top_per_episode(mock_db, monkeypatch):
    request_record = MagicMock(id=21, media_type=MediaType.TV)
    episodes_result = MagicMock()
    episodes_result.scalars.return_value.all.return_value = [1, 2]
    low_ep1 = MagicMock(id=10, episode_number=1, score=50)
    top_ep1 = MagicMock(id=11, episode_number=1, score=90)
    top_ep2 = MagicMock(id=12, episode_number=2, score=80)
    releases_result = MagicMock()
    releases_result.scalars.return_value.all.return_value = [top_ep1, low_ep1, top_ep2]
    mock_db.execute.side_effect = [episodes_result, releases_result]

    staging_instance = AsyncMock()
    staging_instance.stage_individual_episode_releases = AsyncMock(
        return_value={"status": "staged", "action": "replaced_active_selection"}
    )
    monkeypatch.setattr(
        dashboard_actions, "load_request_or_404", AsyncMock(return_value=request_record)
    )
    monkeypatch.setattr(dashboard_actions, "StagingService", lambda db: staging_instance)

    response = await dashboard_actions.stage_individual_episode_releases(
        request_id=21,
        season_number=1,
        http_request=MagicMock(headers={"accept": "application/json"}),
        redirect_to=None,
        db=mock_db,
    )

    body = json.loads(cast(bytes, response.body))
    assert body == {"status": "ok", "message": "Active staged selection replaced successfully"}
    staging_instance.stage_individual_episode_releases.assert_awaited_once_with(
        request_record,
        1,
        [top_ep1, top_ep2],
    )


@pytest.mark.asyncio
async def test_stage_individual_episode_releases_errors_for_missing_episode(mock_db, monkeypatch):
    request_record = MagicMock(id=21, media_type=MediaType.TV)
    episodes_result = MagicMock()
    episodes_result.scalars.return_value.all.return_value = [1, 2]
    releases_result = MagicMock()
    releases_result.scalars.return_value.all.return_value = [MagicMock(id=11, episode_number=1)]
    mock_db.execute.side_effect = [episodes_result, releases_result]
    monkeypatch.setattr(
        dashboard_actions, "load_request_or_404", AsyncMock(return_value=request_record)
    )

    response = await dashboard_actions.stage_individual_episode_releases(
        request_id=21,
        season_number=1,
        http_request=MagicMock(headers={"accept": "application/json"}),
        redirect_to=None,
        db=mock_db,
    )

    body = json.loads(cast(bytes, response.body))
    assert response.status_code == 400
    assert body == {"status": "error", "message": "No eligible releases for episode(s): 2"}


def test_stage_individual_episode_release_filter_requires_non_empty_url():
    compiled = str(
        dashboard_actions._release_has_usable_url().compile(
            dialect=sqlite.dialect(), compile_kwargs={"literal_binds": True}
        )
    )

    assert "length(trim(releases.magnet_url)) > 0" in compiled
    assert "length(trim(releases.download_url)) > 0" in compiled


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
    monkeypatch.setattr(
        dashboard_actions, "_persist_manual_candidate_uncommitted", persist_manual_release
    )
    monkeypatch.setattr(dashboard_actions, "StagingService", lambda db: staging_instance)

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


@pytest.mark.parametrize(
    ("statuses", "season_status", "request_status"),
    [
        # Precedence rules not covered by tests/services/lifecycle/test_episode_derive.py.
        ([S.COMPLETED, S.DOWNLOADING], S.DOWNLOADING, S.PENDING),
        ([S.COMPLETED, S.STAGED], S.STAGED, S.PENDING),
        ([S.COMPLETED, S.PENDING], S.PENDING, S.PENDING),
        ([], S.PENDING, S.PENDING),
    ],
)
def test_season_status_precedence_over_completed_episodes(statuses, season_status, request_status):
    episodes = [MagicMock(status=status) for status in statuses]

    assert derive_season_status(episodes) == season_status
    assert derive_request_status_from_episodes(episodes) == request_status


@pytest.mark.asyncio
async def test_mark_episode_available_rejects_already_completed_episode(db):
    show = await make_request(db, media_type=MediaType.TV)
    season = await make_season(db, show, 1)
    episode = await make_episode(db, season, 1, status=S.COMPLETED)

    with pytest.raises(HTTPException) as exc_info:
        await dashboard_actions.mark_episode_available(
            request_id=show.id, episode_id=episode.id, db=db
        )

    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_mark_episode_available_returns_404_for_unknown_episode(db):
    with pytest.raises(HTTPException) as exc_info:
        await dashboard_actions.mark_episode_available(request_id=1, episode_id=999, db=db)

    assert exc_info.value.status_code == 404
