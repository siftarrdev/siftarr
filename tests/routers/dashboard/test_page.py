"""Tests for dashboard page routes."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.siftarr.models.request import MediaType, RequestStatus
from app.siftarr.routers import dashboard


@pytest.mark.asyncio
async def test_pending_requests_include_searching_requests(mock_db, monkeypatch):
    """Pending tab should keep in-flight searches visible."""
    active_request = MagicMock()
    active_request.id = 1
    active_request.status = RequestStatus.SEARCHING
    active_request.overseerr_request_id = 10
    active_request.title = "The Rookie"
    active_request.media_type.value = "tv"
    active_request.created_at = MagicMock()

    lifecycle_service = AsyncMock()
    lifecycle_service.get_active_requests.return_value = [active_request]
    lifecycle_service.get_requests_by_status.return_value = []
    lifecycle_service.get_requests_stats.return_value = {
        "by_status": {},
    }
    monkeypatch.setattr(dashboard, "LifecycleService", lambda db: lifecycle_service)

    monkeypatch.setattr(
        dashboard,
        "PendingQueueService",
        lambda db: AsyncMock(get_all_pending=AsyncMock(return_value=[])),
    )

    monkeypatch.setattr(
        dashboard,
        "get_effective_settings",
        AsyncMock(
            return_value=MagicMock(
                overseerr_url="http://overseerr.test",
                staging_mode_enabled=False,
            )
        ),
    )

    mock_db.execute.return_value = MagicMock(
        scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))
    )

    response = await dashboard.dashboard(MagicMock(), db=mock_db)

    context = response.context
    assert active_request in context["pending_requests"]


@pytest.mark.asyncio
async def test_dashboard_restores_unreleased_tab_and_requests(mock_db, monkeypatch):
    """Dashboard should expose unreleased requests and stats for the tab."""
    unreleased_request = MagicMock()
    unreleased_request.id = 42
    unreleased_request.status = RequestStatus.UNRELEASED
    unreleased_request.overseerr_request_id = 10
    unreleased_request.title = "The Rookie"
    unreleased_request.media_type = MediaType.TV
    unreleased_request.created_at = datetime(2026, 4, 1, 12, 0, tzinfo=UTC)
    unreleased_request.requester_username = "lucas"
    unreleased_request.year = 2025
    unreleased_request.tmdb_id = 123
    unreleased_request.tvdb_id = 456

    lifecycle_service = AsyncMock()
    lifecycle_service.get_active_requests.return_value = [unreleased_request]
    lifecycle_service.get_requests_by_status.return_value = []
    lifecycle_service.get_unreleased_and_partial_requests.return_value = [unreleased_request]
    lifecycle_service.get_requests_stats.return_value = {
        "by_status": {RequestStatus.UNRELEASED.value: 1},
    }
    monkeypatch.setattr(dashboard, "LifecycleService", lambda db: lifecycle_service)

    monkeypatch.setattr(
        dashboard,
        "PendingQueueService",
        lambda db: AsyncMock(get_all_pending=AsyncMock(return_value=[])),
    )
    monkeypatch.setattr(
        dashboard,
        "get_effective_settings",
        AsyncMock(
            return_value=MagicMock(
                overseerr_url="http://overseerr.test",
                staging_mode_enabled=False,
            )
        ),
    )

    fake_overseerr = AsyncMock()
    fake_overseerr.get_media_details.return_value = {"nextEpisodeToAir": {"airDate": "2026-05-01"}}
    monkeypatch.setattr(dashboard, "OverseerrService", lambda settings: fake_overseerr)

    execute_results = [
        MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))),
        MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))),
    ]
    mock_db.execute.side_effect = execute_results

    response = await dashboard.dashboard(MagicMock(), db=mock_db)

    context = response.context
    assert context["unreleased_requests"] == [unreleased_request]
    assert context["unreleased_earliest"][42] == "2026-05-01"
    assert context["stats"]["unreleased"] == 1
    rendered = response.body.decode()
    assert "tab-unreleased" in rendered
    assert "content-unreleased" in rendered
    assert "The Rookie" in rendered
    assert "No unreleased requests." not in rendered


@pytest.mark.asyncio
async def test_dashboard_separates_mixed_pending_from_true_unreleased(mock_db, monkeypatch):
    """TV shows with pending episodes should stay out of Unreleased tab."""
    mixed_request = MagicMock()
    mixed_request.id = 7
    mixed_request.status = RequestStatus.PARTIALLY_AVAILABLE
    mixed_request.overseerr_request_id = 11
    mixed_request.title = "High Potential"
    mixed_request.media_type = MediaType.TV
    mixed_request.created_at = datetime(2026, 4, 1, 12, 0, tzinfo=UTC)
    mixed_request.requester_username = "lucas"
    mixed_request.year = 2025
    mixed_request.tmdb_id = 123
    mixed_request.tvdb_id = 456

    unreleased_request = MagicMock()
    unreleased_request.id = 8
    unreleased_request.status = RequestStatus.UNRELEASED
    unreleased_request.overseerr_request_id = 12
    unreleased_request.title = "The Rookie"
    unreleased_request.media_type = MediaType.TV
    unreleased_request.created_at = datetime(2026, 4, 1, 13, 0, tzinfo=UTC)
    unreleased_request.requester_username = "lucas"
    unreleased_request.year = 2025
    unreleased_request.tmdb_id = 124
    unreleased_request.tvdb_id = 457

    lifecycle_service = AsyncMock()
    lifecycle_service.get_active_requests.return_value = [mixed_request, unreleased_request]
    lifecycle_service.get_requests_by_status.return_value = []
    lifecycle_service.get_unreleased_and_partial_requests.return_value = [
        mixed_request,
        unreleased_request,
    ]
    lifecycle_service.get_requests_stats.return_value = {
        "by_status": {
            RequestStatus.UNRELEASED.value: 1,
            RequestStatus.PARTIALLY_AVAILABLE.value: 1,
        },
    }
    monkeypatch.setattr(dashboard, "LifecycleService", lambda db: lifecycle_service)

    monkeypatch.setattr(
        dashboard,
        "PendingQueueService",
        lambda db: AsyncMock(get_all_pending=AsyncMock(return_value=[])),
    )
    monkeypatch.setattr(
        dashboard,
        "get_effective_settings",
        AsyncMock(
            return_value=MagicMock(
                overseerr_url="http://overseerr.test",
                staging_mode_enabled=False,
            )
        ),
    )

    fake_overseerr = AsyncMock()
    fake_overseerr.get_media_details.return_value = {"nextEpisodeToAir": {"airDate": "2026-05-01"}}
    monkeypatch.setattr(dashboard, "OverseerrService", lambda settings: fake_overseerr)

    execute_results = [
        MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))),
        MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))),
        MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))),
        MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))),
    ]
    mock_db.execute.side_effect = execute_results

    seasons = [MagicMock(id=1, season_number=8, synced_at=None)]
    episodes = [
        MagicMock(status=RequestStatus.AVAILABLE),
        MagicMock(status=RequestStatus.AVAILABLE),
        MagicMock(status=RequestStatus.PENDING),
        MagicMock(status=RequestStatus.PENDING),
    ]
    monkeypatch.setattr(
        dashboard,
        "load_tv_seasons_with_episodes",
        AsyncMock(side_effect=[(seasons, episodes), (seasons, episodes)]),
    )

    response = await dashboard.dashboard(MagicMock(), db=mock_db)

    context = response.context
    assert mixed_request in context["pending_requests"]
    assert mixed_request not in context["unreleased_requests"]
    assert unreleased_request in context["unreleased_requests"]


@pytest.mark.asyncio
async def test_dashboard_hides_completed_ongoing_tv_from_finished_when_unreleased(
    mock_db, monkeypatch
):
    """Finished-tab rows reclassified as unreleased should move out of completed results."""
    completed_tv = MagicMock()
    completed_tv.id = 21
    completed_tv.status = RequestStatus.COMPLETED
    completed_tv.overseerr_request_id = 99
    completed_tv.title = "The Rookie"
    completed_tv.media_type = MediaType.TV
    completed_tv.created_at = datetime(2026, 4, 1, 12, 0, tzinfo=UTC)
    completed_tv.requester_username = "lucas"
    completed_tv.year = 2025
    completed_tv.tmdb_id = 123
    completed_tv.tvdb_id = 456

    lifecycle_service = AsyncMock()
    lifecycle_service.get_active_requests.return_value = []
    lifecycle_service.get_requests_by_status.return_value = [completed_tv]
    lifecycle_service.get_unreleased_and_partial_requests.return_value = [completed_tv]
    lifecycle_service.get_requests_stats.return_value = {
        "by_status": {
            RequestStatus.COMPLETED.value: 1,
            RequestStatus.UNRELEASED.value: 1,
        }
    }
    monkeypatch.setattr(dashboard, "LifecycleService", lambda db: lifecycle_service)

    monkeypatch.setattr(
        dashboard,
        "PendingQueueService",
        lambda db: AsyncMock(get_all_pending=AsyncMock(return_value=[])),
    )
    monkeypatch.setattr(
        dashboard,
        "get_effective_settings",
        AsyncMock(
            return_value=MagicMock(
                overseerr_url="http://overseerr.test",
                staging_mode_enabled=False,
            )
        ),
    )

    fake_overseerr = AsyncMock()
    fake_overseerr.get_media_details.return_value = {"nextEpisodeToAir": {"airDate": "2026-05-01"}}
    monkeypatch.setattr(dashboard, "OverseerrService", lambda settings: fake_overseerr)

    mock_db.execute.side_effect = [
        MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))),
        MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))),
        MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))),
    ]
    monkeypatch.setattr(
        dashboard,
        "load_tv_seasons_with_episodes",
        AsyncMock(
            return_value=(
                [MagicMock(id=1, season_number=8, synced_at=None)],
                [
                    MagicMock(status=RequestStatus.AVAILABLE),
                    MagicMock(status=RequestStatus.UNRELEASED),
                ],
            )
        ),
    )

    response = await dashboard.dashboard(MagicMock(), db=mock_db)

    context = response.context
    assert completed_tv in context["unreleased_requests"]
    assert completed_tv not in context["completed_requests"]


@pytest.mark.asyncio
async def test_dashboard_renders_staged_torrents_for_refresh(mock_db, monkeypatch):
    """Dashboard should include staged torrents in the staged tab HTML."""
    lifecycle_service = AsyncMock()
    lifecycle_service.get_active_requests.return_value = []
    lifecycle_service.get_requests_by_status.return_value = []
    lifecycle_service.get_unreleased_and_partial_requests.return_value = []
    lifecycle_service.get_requests_stats.return_value = {"by_status": {}}
    monkeypatch.setattr(dashboard, "LifecycleService", lambda db: lifecycle_service)

    monkeypatch.setattr(
        dashboard,
        "PendingQueueService",
        lambda db: AsyncMock(get_all_pending=AsyncMock(return_value=[])),
    )
    monkeypatch.setattr(
        dashboard,
        "get_effective_settings",
        AsyncMock(
            return_value=MagicMock(
                overseerr_url="http://overseerr.test",
                staging_mode_enabled=False,
            )
        ),
    )

    staged_torrent = MagicMock()
    staged_torrent.id = 1
    staged_torrent.request_id = None
    staged_torrent.title = "Test Torrent"
    staged_torrent.status = "staged"
    staged_torrent.size = 123
    staged_torrent.indexer = "Indexer"
    staged_torrent.score = 42
    staged_torrent.created_at = datetime(2026, 4, 1, 12, 0, tzinfo=UTC)
    staged_torrent.replaced_by_id = None
    staged_torrent.replacement_reason = None

    staged_result = MagicMock(
        scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[staged_torrent])))
    )
    empty_result = MagicMock(
        scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))
    )
    mock_db.execute.side_effect = [staged_result, empty_result, empty_result, empty_result]

    response = await dashboard.dashboard(MagicMock(), db=mock_db)

    body = response.body.decode()
    assert "staged-torrents-body" in body
    assert "Test Torrent" in body


@pytest.mark.asyncio
async def test_dashboard_hides_stale_available_and_partial_staged_torrents(
    mock_db, monkeypatch
):
    """Approved request-linked torrents should disappear once requests are resolved."""
    lifecycle_service = AsyncMock()
    lifecycle_service.get_active_requests.return_value = []
    lifecycle_service.get_requests_by_status.return_value = []
    lifecycle_service.get_unreleased_and_partial_requests.return_value = []
    lifecycle_service.get_requests_stats.return_value = {"by_status": {}}
    monkeypatch.setattr(dashboard, "LifecycleService", lambda db: lifecycle_service)

    monkeypatch.setattr(
        dashboard,
        "PendingQueueService",
        lambda db: AsyncMock(get_all_pending=AsyncMock(return_value=[])),
    )
    monkeypatch.setattr(
        dashboard,
        "get_effective_settings",
        AsyncMock(
            return_value=MagicMock(
                overseerr_url="http://overseerr.test",
                staging_mode_enabled=False,
            )
        ),
    )

    active_torrent = MagicMock()
    active_torrent.id = 1
    active_torrent.request_id = 100
    active_torrent.title = "Still Downloading"
    active_torrent.status = "approved"
    active_torrent.size = 123
    active_torrent.indexer = "Indexer"
    active_torrent.score = 50
    active_torrent.created_at = datetime(2026, 4, 1, 12, 0, tzinfo=UTC)
    active_torrent.replaced_by_id = None
    active_torrent.replacement_reason = None

    available_torrent = MagicMock()
    available_torrent.id = 2
    available_torrent.request_id = 101
    available_torrent.title = "Already Available"
    available_torrent.status = "approved"
    available_torrent.size = 123
    available_torrent.indexer = "Indexer"
    available_torrent.score = 45
    available_torrent.created_at = datetime(2026, 4, 1, 11, 0, tzinfo=UTC)
    available_torrent.replaced_by_id = None
    available_torrent.replacement_reason = None

    partial_torrent = MagicMock()
    partial_torrent.id = 3
    partial_torrent.request_id = 102
    partial_torrent.title = "Partially Available"
    partial_torrent.status = "approved"
    partial_torrent.size = 123
    partial_torrent.indexer = "Indexer"
    partial_torrent.score = 40
    partial_torrent.created_at = datetime(2026, 4, 1, 10, 0, tzinfo=UTC)
    partial_torrent.replaced_by_id = None
    partial_torrent.replacement_reason = None

    staged_result = MagicMock(
        scalars=MagicMock(
            return_value=MagicMock(
                all=MagicMock(return_value=[active_torrent, available_torrent, partial_torrent])
            )
        )
    )
    request_status_result = MagicMock()
    request_status_result.all.return_value = [
        (100, RequestStatus.DOWNLOADING),
        (101, RequestStatus.AVAILABLE),
        (102, RequestStatus.PARTIALLY_AVAILABLE),
    ]
    empty_result = MagicMock(
        scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))
    )
    mock_db.execute.side_effect = [staged_result, request_status_result, empty_result]

    response = await dashboard.dashboard(MagicMock(), db=mock_db)

    context = response.context
    assert context["staged_torrents"] == [active_torrent]
    body = response.body.decode()
    assert "Still Downloading" in body
    assert "Already Available" not in body
    assert "Partially Available" not in body
