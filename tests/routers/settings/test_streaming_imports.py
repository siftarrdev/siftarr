"""Settings streaming and import flow tests."""

import asyncio
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.siftarr.models.request import MediaType
from app.siftarr.routers import settings


@pytest.mark.asyncio
async def test_rescan_plex_sse_reports_movies_and_tv_in_active_items(monkeypatch, mock_db):
    """Plex SSE progress should include both movie and TV requests in active items."""

    runtime_settings = MagicMock(plex_sync_concurrency=2)
    plex_service = AsyncMock()

    movie_request = MagicMock(id=1, title="Movie One", media_type=MediaType.MOVIE, status="pending")
    tv_request = MagicMock(id=2, title="Show One", media_type=MediaType.TV, status="pending")

    polling = AsyncMock()
    polling.get_active_requests = AsyncMock(return_value=[movie_request, tv_request])
    polling.poll = AsyncMock(return_value=7)
    monkeypatch.setattr(settings, "PlexPollingService", lambda db, plex: polling)
    monkeypatch.setattr(settings, "PlexService", lambda settings: plex_service)

    monkeypatch.setattr(settings, "async_session_maker", lambda: AsyncMock())

    tv_rescan = AsyncMock(return_value=True)
    monkeypatch.setattr(settings, "_rescan_plex_tv_request", tv_rescan)

    events: list[dict[str, Any]] = []

    async def collect(payload):
        events.append(payload)

    resynced, failed, completed = await settings._rescan_plex_requests(
        mock_db,
        runtime_settings,
        plex_service,
        on_event=collect,
    )

    assert (resynced, failed, completed) == (1, 0, 7)
    assert any(
        event.get("phase") == "fetching" and event.get("active") == ["Movie One", "Show One"]
        for event in events
    )
    assert any(event.get("phase") == "processing" and event.get("active") for event in events)
    assert tv_rescan.await_count == 1
    tv_rescan.assert_awaited_once_with(2, plex_service, runtime_settings)


@pytest.mark.asyncio
async def test_rescan_plex_uses_bounded_parallel_workers_and_reports_counts(
    monkeypatch, mock_db, base_context
):
    """Plex rescan should cap per-TV sync concurrency, isolate sessions, and report counts."""

    monkeypatch.setattr(
        settings,
        "_build_settings_page_context",
        AsyncMock(return_value=base_context()),
    )
    runtime_settings = MagicMock(plex_sync_concurrency=2)
    monkeypatch.setattr(
        settings._jobs,
        "get_settings",
        lambda: runtime_settings,
    )

    plex_service = AsyncMock()
    monkeypatch.setattr(settings, "PlexService", lambda settings: plex_service)

    tv_requests = []
    for request_id in (11, 12, 13, 14):
        tv_request = MagicMock()
        tv_request.id = request_id
        tv_request.media_type = MediaType.TV
        tv_request.status = "pending"
        tv_requests.append(tv_request)

    scalars = MagicMock()
    scalars.all.return_value = tv_requests
    execute_result = MagicMock()
    execute_result.scalars.return_value = scalars
    mock_db.execute.return_value = execute_result

    worker_dbs = []
    request_to_db = {}
    started = 0
    in_flight = 0
    max_in_flight = 0
    finished = 0
    first_batch_ready = asyncio.Event()
    third_worker_started = asyncio.Event()
    release_workers = asyncio.Event()
    poll_called = False

    class FakeWorkerDB:
        def __init__(self, label):
            self.label = label
            self.rollback = AsyncMock()

    class FakeWorkerSessionContext:
        def __init__(self, worker_db):
            self.worker_db = worker_db

        async def __aenter__(self):
            worker_dbs.append(self.worker_db)
            return self.worker_db

        async def __aexit__(self, exc_type, exc, tb):
            return False

    worker_counter = 0

    def fake_async_session_maker():
        nonlocal worker_counter
        worker_counter += 1
        return FakeWorkerSessionContext(FakeWorkerDB(worker_counter))

    monkeypatch.setattr(settings, "async_session_maker", fake_async_session_maker)

    class FakeEpisodeSyncService:
        def __init__(self, db, overseerr=None, plex=None):
            self.db = db
            assert db is not mock_db
            assert plex is plex_service

        async def sync_request(self, request_id):
            nonlocal started, in_flight, max_in_flight, finished
            request_to_db[request_id] = self.db
            started += 1
            in_flight += 1
            max_in_flight = max(max_in_flight, in_flight)
            if started == runtime_settings.plex_sync_concurrency:
                first_batch_ready.set()
            if started == runtime_settings.plex_sync_concurrency + 1:
                third_worker_started.set()
            try:
                await release_workers.wait()
                if request_id == 13:
                    raise RuntimeError("boom")
            finally:
                in_flight -= 1
                finished += 1

    import app.siftarr.services.episode_sync_service as episode_sync_module

    monkeypatch.setattr(episode_sync_module, "EpisodeSyncService", FakeEpisodeSyncService)

    class FakePollingService:
        def __init__(self, db, plex):
            assert db is mock_db
            assert plex is plex_service

        async def get_active_requests(self):
            return tv_requests

        async def poll(self, on_progress=None):
            nonlocal poll_called
            poll_called = True
            assert finished == len(tv_requests)
            return 4

    monkeypatch.setattr(settings, "PlexPollingService", FakePollingService)

    rescan_task = asyncio.create_task(settings.rescan_plex(MagicMock(), db=mock_db))
    await asyncio.wait_for(first_batch_ready.wait(), timeout=1)

    assert started == 2
    assert max_in_flight == 2
    assert third_worker_started.is_set() is False
    assert poll_called is False

    release_workers.set()
    response = await rescan_task
    context = cast(dict, getattr(response, "context", None))

    assert context["message_type"] == "success"
    assert "Re-synced 3 TV request(s)" in context["message"]
    assert "had 1 failed TV request(s)" in context["message"]
    assert "transitioned 4 request(s) to completed" in context["message"]
    assert max_in_flight == 2
    assert len(worker_dbs) == 4
    assert len({id(worker_db) for worker_db in worker_dbs}) == 4
    assert set(request_to_db) == {11, 12, 13, 14}
    assert all(worker_db is not mock_db for worker_db in worker_dbs)
    request_to_db[13].rollback.assert_awaited_once()
    plex_service.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_sync_overseerr_prefetches_with_bounded_parallelism(monkeypatch, base_context):
    """Overseerr sync should cap metadata fetch concurrency and keep DB writes serial."""

    class FakeDB:
        def __init__(self):
            self.added = []
            self.next_id = 1
            self.commit = AsyncMock()
            self.refresh = AsyncMock()

        async def execute(self, _statement):
            result = MagicMock()
            result.fetchall.return_value = []
            return result

        def add(self, obj):
            self.added.append(obj)

        async def flush(self):
            pending = [obj for obj in self.added if getattr(obj, "id", None) is None]
            for obj in pending:
                obj.id = self.next_id
                self.next_id += 1

    mock_db = FakeDB()
    context = base_context()
    context["env"] = {"overseerr_url": "http://ov", "overseerr_api_key": "key"}

    monkeypatch.setattr(
        settings,
        "_build_settings_page_context",
        AsyncMock(return_value=context),
    )
    runtime_settings = MagicMock(overseerr_sync_concurrency=2)
    monkeypatch.setattr(settings._imports, "get_settings", lambda: runtime_settings)

    started = 0
    in_flight = 0
    max_in_flight = 0
    release_event = asyncio.Event()

    overseerr_requests = [
        {
            "id": 100,
            "status": "approved",
            "media": {"tmdbId": 1, "mediaType": "movie"},
            "requestedBy": {"username": "movie-user"},
        },
        {
            "id": 101,
            "status": "approved",
            "media": {"tmdbId": 2, "mediaType": "tv"},
            "requestedBy": {"username": "tv-user-1"},
        },
        {
            "id": 102,
            "status": "pending",
            "media": {"tmdbId": 3, "mediaType": "tv"},
            "requestedBy": {"username": "tv-user-2"},
        },
    ]
    details_by_id = {
        1: {"title": "Movie One", "releaseDate": "2024-01-02", "status": "Released"},
        2: {"name": "Show One", "firstAirDate": "2023-02-03", "status": "Returning Series"},
        3: {"name": "Show Two", "firstAirDate": "2025-04-05", "status": "Returning Series"},
    }

    class FakeOverseerrService:
        def __init__(self, settings=None):
            self.settings = settings

        async def get_all_requests(self, status=None):
            assert status is None
            return overseerr_requests

        def normalize_request_status(self, status):
            return str(status).lower()

        def normalize_media_status(self, status):
            return str(status).lower() if status is not None else "unknown"

        async def get_media_details(self, media_type, external_id):
            nonlocal started, in_flight, max_in_flight
            started += 1
            in_flight += 1
            max_in_flight = max(max_in_flight, in_flight)
            if started == runtime_settings.overseerr_sync_concurrency:
                release_event.set()
            await release_event.wait()
            in_flight -= 1
            return details_by_id[external_id]

        async def close(self):
            return None

    monkeypatch.setattr(settings, "OverseerrService", FakeOverseerrService)

    evaluate_calls = []

    async def fake_evaluate_imported_request(db, overseerr, request, **kwargs):
        evaluate_calls.append(
            {
                "request_id": request.id,
                "media_type": request.media_type,
                "title": request.title,
                "prefetched_media_details": kwargs.get("prefetched_media_details"),
                "local_episodes": kwargs.get("local_episodes"),
            }
        )
        return None

    monkeypatch.setattr(settings, "evaluate_imported_request", fake_evaluate_imported_request)

    plex_service = AsyncMock()
    monkeypatch.setattr(settings, "PlexService", lambda settings: plex_service)

    synced_episode_ids = []

    class FakeEpisodeSyncService:
        def __init__(self, db, overseerr=None, plex=None):
            self.db = db
            self.overseerr = overseerr
            self.plex = plex

        async def sync_request(self, request_id):
            synced_episode_ids.append(request_id)

    import app.siftarr.services.episode_sync_service as episode_sync_module

    monkeypatch.setattr(episode_sync_module, "EpisodeSyncService", FakeEpisodeSyncService)

    response = await settings.sync_overseerr(MagicMock(), db=cast(Any, mock_db))
    response_context = cast(dict, getattr(response, "context", None))

    assert response_context["message_type"] == "success"
    assert response_context["message"] == "Synced 3 new request(s) from Overseerr"
    assert max_in_flight == 2
    assert synced_episode_ids == [2, 3]
    assert [call["title"] for call in evaluate_calls] == ["Movie One", "Show One", "Show Two"]
    assert all(call["prefetched_media_details"] is not None for call in evaluate_calls)
    assert all(call["local_episodes"] == () for call in evaluate_calls)
    assert [request.media_type for request in mock_db.added] == [
        MediaType.MOVIE,
        MediaType.TV,
        MediaType.TV,
    ]
    mock_db.commit.assert_awaited_once()
    plex_service.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_sync_overseerr_keeps_duplicate_skipping_behavior(monkeypatch, base_context):
    """Overseerr sync should still skip already-imported actionable requests."""

    class FakeDB:
        def __init__(self):
            self.added = []
            self.commit = AsyncMock()
            self.refresh = AsyncMock()

        async def execute(self, _statement):
            result = MagicMock()
            result.fetchall.return_value = [("1", None), ("999", 202)]
            return result

        def add(self, obj):
            self.added.append(obj)

        async def flush(self):
            msg = "flush should not run when all requests are skipped"
            raise AssertionError(msg)

    mock_db = FakeDB()
    context = base_context()
    context["env"] = {"overseerr_url": "http://ov", "overseerr_api_key": "key"}

    monkeypatch.setattr(
        settings,
        "_build_settings_page_context",
        AsyncMock(return_value=context),
    )
    runtime_settings = MagicMock(overseerr_sync_concurrency=2)
    monkeypatch.setattr(settings._imports, "get_settings", lambda: runtime_settings)

    overseerr_requests = [
        {
            "id": 201,
            "status": "approved",
            "media": {"tmdbId": 1, "mediaType": "movie"},
        },
        {
            "id": 202,
            "status": "pending",
            "media": {"tmdbId": 999, "mediaType": "tv"},
        },
    ]

    class FakeOverseerrService:
        def __init__(self, settings=None):
            self.settings = settings

        async def get_all_requests(self, status=None):
            return overseerr_requests

        def normalize_request_status(self, status):
            return str(status).lower()

        def normalize_media_status(self, status):
            return str(status).lower() if status is not None else "unknown"

        async def get_media_details(self, media_type, external_id):
            return {"title": f"Title {external_id}", "status": "Released"}

        async def close(self):
            return None

    monkeypatch.setattr(settings, "OverseerrService", FakeOverseerrService)
    evaluate_mock = AsyncMock()
    monkeypatch.setattr(settings, "evaluate_imported_request", evaluate_mock)

    response = await settings.sync_overseerr(MagicMock(), db=cast(Any, mock_db))
    response_context = cast(dict, getattr(response, "context", None))

    assert response_context["message_type"] == "success"
    assert response_context["message"] == (
        "No new actionable requests to sync (2 already existed or were already available)"
    )
    assert mock_db.added == []
    mock_db.commit.assert_awaited_once()
    evaluate_mock.assert_not_awaited()
