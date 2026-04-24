"""Settings streaming and import flow tests."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from app.siftarr.models.episode import Episode
from app.siftarr.models.request import MediaType, RequestStatus
from app.siftarr.models.request import Request as RequestModel
from app.siftarr.models.season import Season
from app.siftarr.routers import settings
from app.siftarr.services import settings_service
from app.siftarr.services.plex_service import PlexLookupResult, PlexService


def _parse_sse_events(chunks: list[str]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for chunk in chunks:
        for line in chunk.splitlines():
            if line.startswith("data: "):
                events.append(json.loads(line.removeprefix("data: ")))
    return events


@pytest.mark.asyncio
async def test_bounded_progress_reports_completed_counts_before_terminal_events():
    """Bounded progress should not report 100% before a terminal complete/error event."""

    items = [MagicMock(id=1, title="One"), MagicMock(id=2, title="Two")]
    events: list[dict[str, Any]] = []
    release_workers = asyncio.Event()

    async def collect(payload: dict[str, Any]) -> None:
        events.append(payload)

    async def worker(_item):
        await release_workers.wait()
        return True

    task = asyncio.create_task(
        settings._run_bounded_with_progress(
            items,
            2,
            worker,
            on_event=collect,
            phase="processing",
        )
    )

    while len(events) < 2:
        await asyncio.sleep(0)

    non_terminal_events = [event for event in events if event.get("phase") == "processing"]
    assert all(event["current"] < event["total"] for event in non_terminal_events)
    assert all("started" in event and "completed" in event for event in non_terminal_events)
    assert [event["completed"] for event in non_terminal_events] == [0, 0]

    release_workers.set()
    assert await task == [True, True]
    assert events[-1]["current"] == 2
    assert events[-1]["completed"] == 2
    assert events[-1]["active"] == []


@pytest.mark.asyncio
async def test_sync_overseerr_sse_streams_fetch_prefetch_import_tv_sync_and_completion(
    monkeypatch,
):
    """Overseerr SSE should expose usable progress for each long-running stage."""

    class FakeSessionContext:
        async def __aenter__(self):
            return MagicMock()

        async def __aexit__(self, exc_type, exc, tb):
            return False

    runtime_settings = MagicMock(overseerr_sync_concurrency=2)
    monkeypatch.setattr(settings_service, "get_settings", lambda: runtime_settings)

    async def build_effective_settings(_db):
        return {"overseerr_url": "http://overseerr", "overseerr_api_key": "key"}

    async def import_overseerr_requests(_db, _runtime_settings, **kwargs):
        emit = kwargs.get("on_event") or kwargs.get("on_progress")
        if emit is not None:
            for payload in [
                settings_service.build_sse_progress(
                    "fetching",
                    current=0,
                    total=4,
                    message="Fetching requests from Overseerr...",
                    active=[],
                ),
                settings_service.build_sse_progress(
                    "prefetching",
                    current=1,
                    total=3,
                    message="Fetching metadata for Movie One...",
                    active=["Movie One", "Show One"],
                ),
                settings_service.build_sse_progress(
                    "importing",
                    current=1,
                    total=2,
                    message="Importing Show One...",
                    active=["Show One"],
                ),
                settings_service.build_sse_progress(
                    "episode_sync",
                    current=0,
                    total=1,
                    message="Syncing TV episodes for Show One...",
                    active=["Show One"],
                ),
            ]:
                await emit(payload)
        return 2, 1

    chunks = [
        chunk
        async for chunk in settings_service.sync_overseerr_generator(
            async_session_maker=FakeSessionContext,
            build_effective_settings_func=build_effective_settings,
            import_overseerr_requests_func=import_overseerr_requests,
            build_sse_progress_func=settings_service.build_sse_progress,
            logger=settings.logger,
        )
    ]
    events = _parse_sse_events(chunks)

    phases = [event["phase"] for event in events]
    assert phases == [
        "connecting",
        "fetching",
        "prefetching",
        "importing",
        "episode_sync",
        "complete",
    ]
    for event in events[1:-1]:
        assert isinstance(event["current"], int)
        assert isinstance(event["total"], int)
        assert event["current"] < event["total"]
        assert event["message"]
        assert isinstance(event["active"], list)
    assert events[-1]["message"] == "Synced 2 new request(s) from Overseerr"


@pytest.mark.asyncio
@pytest.mark.parametrize("shallow", [True, False])
async def test_rescan_plex_sse_streams_partial_and_full_progress(monkeypatch, shallow):
    """Plex SSE should stream bounded TV resync progress and the final poll/refresh step."""

    class FakeSessionContext:
        async def __aenter__(self):
            return MagicMock()

        async def __aexit__(self, exc_type, exc, tb):
            return False

    runtime_settings = MagicMock(plex_sync_concurrency=2)
    monkeypatch.setattr(settings_service, "get_settings", lambda: runtime_settings)
    plex = AsyncMock()

    async def rescan_plex_requests(_db, _runtime_settings, _plex, *, on_event, shallow):
        await on_event(
            settings_service.build_sse_progress(
                "fetching",
                current=0,
                total=2,
                message="Fetching active Plex requests...",
                active=["Show One", "Show Two"],
                mode="partial" if shallow else "full",
            )
        )
        await on_event(
            settings_service.build_sse_progress(
                "processing",
                current=0,
                total=2,
                started=1,
                completed=0,
                message="Re-syncing Show One...",
                active=["Show One"],
            )
        )
        await on_event(
            settings_service.build_sse_progress(
                "processing",
                current=1,
                total=2,
                started=1,
                completed=1,
                message="Completed Show One.",
                active=[],
            )
        )
        await on_event(
            settings_service.build_sse_progress(
                "polling",
                current=0,
                total=1,
                message="Running Plex poll and metadata refresh...",
                active=[],
            )
        )
        return 1, 0, 2

    chunks = [
        chunk
        async for chunk in settings_service.rescan_plex_generator(
            shallow=shallow,
            async_session_maker=FakeSessionContext,
            plex_service_cls=lambda settings: plex,
            rescan_plex_requests_func=rescan_plex_requests,
            build_sse_progress_func=settings_service.build_sse_progress,
            logger=settings.logger,
        )
    ]
    events = _parse_sse_events(chunks)

    phases = [event["phase"] for event in events]
    assert phases == ["connecting", "fetching", "processing", "processing", "polling", "complete"]
    assert events[1]["mode"] == ("partial" if shallow else "full")
    processing_events = [event for event in events if event["phase"] == "processing"]
    assert [event["current"] for event in processing_events] == [0, 1]
    assert [event["completed"] for event in processing_events] == [0, 1]
    assert all(event["current"] < event["total"] for event in events[1:-1])
    assert events[-2]["message"] == "Running Plex poll and metadata refresh..."
    assert events[-1]["phase"] == "complete"
    assert events[-1]["completed"] == 2
    plex.close.assert_awaited_once()


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
        settings,
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

        async def execute(self, statement):
            columns = getattr(statement, "column_descriptions", [])
            if len(columns) > 1:
                result = MagicMock()
                result.fetchall.return_value = []
                return result

            params = statement.compile().params
            entity = columns[0].get("entity") if columns else None

            if entity is RequestModel:
                request_id = next(iter(params.values()))
                request = next(
                    (
                        row
                        for row in self.added
                        if isinstance(row, RequestModel) and row.id == request_id
                    ),
                    None,
                )
                return MagicMock(scalar_one_or_none=MagicMock(return_value=request))

            if entity is Season:
                request_id = params.get("request_id_1")
                season_number = params.get("season_number_1")
                season = next(
                    (
                        row
                        for row in self.added
                        if isinstance(row, Season)
                        and row.request_id == request_id
                        and row.season_number == season_number
                    ),
                    None,
                )
                return MagicMock(scalar_one_or_none=MagicMock(return_value=season))

            if entity is Episode:
                season_id = params.get("season_id_1")
                episode_number = params.get("episode_number_1")
                episode = next(
                    (
                        row
                        for row in self.added
                        if isinstance(row, Episode)
                        and row.season_id == season_id
                        and row.episode_number == episode_number
                    ),
                    None,
                )
                return MagicMock(scalar_one_or_none=MagicMock(return_value=episode))

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
    monkeypatch.setattr(settings, "get_settings", lambda: runtime_settings)

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
    monkeypatch.setattr(settings, "get_settings", lambda: runtime_settings)

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


@pytest.mark.asyncio
async def test_sync_overseerr_logs_request_level_degraded_tv_sync_once(
    monkeypatch, base_context, caplog
):
    """Overseerr import should surface one request-level degraded sync warning."""

    class FakeDB:
        def __init__(self):
            self.added = []
            self.next_id = 1
            self.commit = AsyncMock()
            self.refresh = AsyncMock()

        async def execute(self, statement):
            columns = getattr(statement, "column_descriptions", [])
            if len(columns) > 1:
                result = MagicMock()
                result.fetchall.return_value = []
                return result

            params = statement.compile().params
            entity = columns[0].get("entity") if columns else None

            if entity is RequestModel:
                request_id = next(iter(params.values()))
                request = next(
                    (
                        row
                        for row in self.added
                        if isinstance(row, RequestModel) and row.id == request_id
                    ),
                    None,
                )
                return MagicMock(scalar_one_or_none=MagicMock(return_value=request))

            if entity is Season:
                request_id = params.get("request_id_1")
                season_number = params.get("season_number_1")
                season = next(
                    (
                        row
                        for row in self.added
                        if isinstance(row, Season)
                        and row.request_id == request_id
                        and row.season_number == season_number
                    ),
                    None,
                )
                return MagicMock(scalar_one_or_none=MagicMock(return_value=season))

            if entity is Episode:
                season_id = params.get("season_id_1")
                episode_number = params.get("episode_number_1")
                episode = next(
                    (
                        row
                        for row in self.added
                        if isinstance(row, Episode)
                        and row.season_id == season_id
                        and row.episode_number == episode_number
                    ),
                    None,
                )
                return MagicMock(scalar_one_or_none=MagicMock(return_value=episode))

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
    monkeypatch.setattr(settings, "get_settings", lambda: runtime_settings)

    overseerr_requests = [
        {
            "id": 101,
            "status": "approved",
            "media": {"tmdbId": 2, "mediaType": "tv"},
            "requestedBy": {"username": "tv-user-1"},
        }
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
            assert media_type == "tv"
            assert external_id == 2
            return {"name": "Show One", "firstAirDate": "2023-02-03", "status": "Returning Series"}

        async def close(self):
            return None

    monkeypatch.setattr(settings, "OverseerrService", FakeOverseerrService)
    monkeypatch.setattr(settings, "evaluate_imported_request", AsyncMock(return_value=None))

    plex_service = AsyncMock()
    monkeypatch.setattr(settings, "PlexService", lambda settings: plex_service)

    class FakeEpisodeSyncService:
        def __init__(self, db, overseerr=None, plex=None):
            self.db = db
            self.overseerr = overseerr
            self.plex = plex

        async def sync_request(self, request_id):
            settings.logger.warning(
                "EpisodeSyncService: degraded Plex sync for request %s (%s); Plex episode availability was inconclusive, preserving existing episode/request state",
                request_id,
                "Show One",
            )

    import app.siftarr.services.episode_sync_service as episode_sync_module

    monkeypatch.setattr(episode_sync_module, "EpisodeSyncService", FakeEpisodeSyncService)

    response = await settings.sync_overseerr(MagicMock(), db=cast(Any, mock_db))
    response_context = cast(dict, getattr(response, "context", None))

    assert response_context["message_type"] == "success"
    assert response_context["message"] == "Synced 1 new request(s) from Overseerr"
    degraded_logs = [
        record.message
        for record in caplog.records
        if "degraded Plex sync for request" in record.message
    ]
    assert degraded_logs == [
        "EpisodeSyncService: degraded Plex sync for request 1 (Show One); "
        "Plex episode availability was inconclusive, preserving existing episode/request state"
    ]
    mock_db.commit.assert_awaited_once()
    plex_service.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_sync_overseerr_fresh_tv_import_bounds_work_and_preserves_state_on_transient_plex_failure(
    monkeypatch, base_context, caplog
):
    """Fresh TV import should fail fast on Plex ReadError without per-season spam."""

    class FakeDB:
        def __init__(self):
            self.added = []
            self.next_id = 1
            self.commit = AsyncMock()
            self.refresh = AsyncMock()

        async def execute(self, statement):
            columns = getattr(statement, "column_descriptions", [])
            if len(columns) > 1:
                result = MagicMock()
                result.fetchall.return_value = []
                return result

            params = statement.compile().params
            entity = columns[0].get("entity") if columns else None

            if entity is RequestModel:
                request_id = next(iter(params.values()))
                request = next(
                    (
                        row
                        for row in self.added
                        if isinstance(row, RequestModel) and row.id == request_id
                    ),
                    None,
                )
                return MagicMock(scalar_one_or_none=MagicMock(return_value=request))

            if entity is Season:
                request_id = params.get("request_id_1")
                season_number = params.get("season_number_1")
                season = next(
                    (
                        row
                        for row in self.added
                        if isinstance(row, Season)
                        and row.request_id == request_id
                        and row.season_number == season_number
                    ),
                    None,
                )
                return MagicMock(scalar_one_or_none=MagicMock(return_value=season))

            if entity is Episode:
                season_id = params.get("season_id_1")
                episode_number = params.get("episode_number_1")
                episode = next(
                    (
                        row
                        for row in self.added
                        if isinstance(row, Episode)
                        and row.season_id == season_id
                        and row.episode_number == episode_number
                    ),
                    None,
                )
                return MagicMock(scalar_one_or_none=MagicMock(return_value=episode))

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
    runtime_settings = MagicMock(
        overseerr_sync_concurrency=2,
        plex_sync_concurrency=2,
        plex_url="http://plex:32400",
        plex_token="token",
    )
    monkeypatch.setattr(settings, "get_settings", lambda: runtime_settings)

    future_day = (datetime.now(UTC) + timedelta(days=7)).date().isoformat()
    overseerr_requests = [
        {
            "id": 101,
            "status": "approved",
            "media": {"tmdbId": 2, "mediaType": "tv"},
            "requestedBy": {"username": "tv-user-1"},
        }
    ]

    class FakeOverseerrService:
        def __init__(self, settings=None):
            self.settings = settings
            self.season_detail_calls: list[int] = []

        async def get_all_requests(self, status=None):
            return overseerr_requests

        def normalize_request_status(self, status):
            return str(status).lower()

        def normalize_media_status(self, status):
            return str(status).lower() if status is not None else "unknown"

        async def get_media_details(self, media_type, external_id):
            assert media_type == "tv"
            assert external_id == 2
            return {
                "name": "Show One",
                "firstAirDate": "2023-02-03",
                "status": "Returning Series",
                "seasons": [
                    {"seasonNumber": 1, "name": "Season 1"},
                    {"seasonNumber": 2, "name": "Season 2"},
                    {"seasonNumber": 3, "name": "Season 3"},
                ],
            }

        async def get_season_details(self, external_id, season_number):
            assert external_id == 2
            self.season_detail_calls.append(season_number)
            return {
                "seasonNumber": season_number,
                "episodes": [
                    {
                        "episodeNumber": 1,
                        "title": f"Episode {season_number}",
                        "airDate": future_day if season_number == 2 else "2024-01-01",
                    }
                ],
            }

        async def close(self):
            return None

    fake_overseerr = FakeOverseerrService()
    monkeypatch.setattr(settings, "OverseerrService", lambda settings=None: fake_overseerr)
    monkeypatch.setattr(settings, "evaluate_imported_request", AsyncMock(return_value=None))

    plex_service = PlexService(settings=runtime_settings)
    lookup_show_by_tmdb = AsyncMock(
        return_value=PlexLookupResult(item={"rating_key": "show-123"}, authoritative=True)
    )
    season_1_started = asyncio.Event()
    season_1_cancelled = asyncio.Event()
    season_3_started = asyncio.Event()
    plex_call_keys: list[str] = []

    async def plex_get(url: str, **kwargs):
        del kwargs
        rating_key = url.rstrip("/").split("/")[-2]
        plex_call_keys.append(rating_key)

        if rating_key == "show-123":
            response = MagicMock()
            response.status_code = 200
            response.json.return_value = {
                "MediaContainer": {
                    "Metadata": [
                        {"type": "season", "index": 1, "ratingKey": "season-1"},
                        {"type": "season", "index": 2, "ratingKey": "season-2"},
                        {"type": "season", "index": 3, "ratingKey": "season-3"},
                    ]
                }
            }
            return response

        if rating_key == "season-1":
            season_1_started.set()
            try:
                await asyncio.Future[None]()
            except asyncio.CancelledError:
                season_1_cancelled.set()
                raise

        if rating_key == "season-2":
            raise httpx.ReadError("boom", request=httpx.Request("GET", url))

        if rating_key == "season-3":
            season_3_started.set()

        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {
            "MediaContainer": {"Metadata": [{"type": "episode", "index": 1, "Media": [{"id": 1}]}]}
        }
        return response

    mock_client = AsyncMock()
    mock_client.get.side_effect = plex_get
    close = AsyncMock()
    monkeypatch.setattr(plex_service, "lookup_show_by_tmdb", lookup_show_by_tmdb)
    monkeypatch.setattr(plex_service, "_get_client", AsyncMock(return_value=mock_client))
    monkeypatch.setattr(plex_service, "close", close)
    monkeypatch.setattr(settings, "PlexService", lambda settings: plex_service)

    response = await settings.sync_overseerr(MagicMock(), db=cast(Any, mock_db))
    response_context = cast(dict, getattr(response, "context", None))

    assert response_context["message_type"] == "success"
    assert response_context["message"] == "Synced 1 new request(s) from Overseerr"
    assert fake_overseerr.season_detail_calls == [1, 2, 3]
    lookup_show_by_tmdb.assert_awaited_once_with(2)
    await asyncio.wait_for(season_1_started.wait(), timeout=1)
    await asyncio.wait_for(season_1_cancelled.wait(), timeout=1)
    assert plex_call_keys == ["show-123", "season-1", "season-2"]
    assert season_3_started.is_set() is False

    tv_request = next(
        row for row in mock_db.added if getattr(row, "media_type", None) == MediaType.TV
    )
    seasons = [row for row in mock_db.added if isinstance(row, Season)]
    episodes = [row for row in mock_db.added if isinstance(row, Episode)]
    assert len(seasons) == 3
    assert len(episodes) == 3
    assert [season.status for season in seasons] == [
        RequestStatus.PENDING,
        RequestStatus.UNRELEASED,
        RequestStatus.PENDING,
    ]
    assert [episode.status for episode in episodes] == [
        RequestStatus.PENDING,
        RequestStatus.UNRELEASED,
        RequestStatus.PENDING,
    ]
    assert tv_request.status == RequestStatus.PENDING

    degraded_logs = [
        record.message
        for record in caplog.records
        if "Plex episode availability was inconclusive" in record.message
    ]
    assert degraded_logs == [
        "EpisodeSyncService: degraded Plex sync for request 1 (Show One); "
        "Plex episode availability was inconclusive, preserving existing episode/request state"
    ]
    spam_logs = [
        record.message
        for record in caplog.records
        if "get_metadata_children(" in record.message or "get_season_children(" in record.message
    ]
    assert spam_logs == []
    assert mock_db.commit.await_count == 2
    close.assert_awaited_once()
