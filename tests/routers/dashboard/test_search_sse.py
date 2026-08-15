"""Tests for SSE streaming search endpoints."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.siftarr.models.request import MediaType
from app.siftarr.routers import search_sse
from app.siftarr.services.dashboard import search_service as search_service_mod


@pytest.mark.asyncio
async def test_stream_search_request_yields_phases_and_result(mock_db, monkeypatch):
    request_record = MagicMock()
    request_record.title = "Test Movie"
    request_record.year = 2020
    request_record.tmdb_id = 123
    request_record.media_type.value = "movie"

    async def fake_load(db, req_id):
        return request_record

    monkeypatch.setattr(search_sse, "load_request_or_404", fake_load)

    process_search = AsyncMock(return_value={"status": "completed", "message": "Found 5 releases"})
    monkeypatch.setattr(search_sse.SearchService, "process_request_search", process_search)

    response = await search_sse.stream_search_request(request_id=1, db=mock_db)
    chunks = []
    async for chunk in response.body_iterator:
        chunks.append(chunk)
    body = "".join(chunks)

    assert '"phase": "starting"' in body
    assert '"percent": 5' in body
    assert '"phase": "searching"' in body
    assert '"percent": 50' in body
    assert '"phase": "complete"' in body
    assert '"percent": 100' in body
    assert '"status": "completed"' in body
    assert '"message": "Found 5 releases"' in body

    # Verify ordering of phases
    starting_idx = body.find('"phase": "starting"')
    searching_idx = body.find('"phase": "searching"')
    complete_idx = body.find('"phase": "complete"')
    assert starting_idx < searching_idx < complete_idx


@pytest.mark.asyncio
async def test_stream_search_request_tv_search_for_new_reload_signal(mock_db, monkeypatch):
    """Request-level TV stream defaults to Search for new and reloads DB buckets."""
    request_record = MagicMock()
    request_record.title = "Test Show"
    request_record.year = 2020
    request_record.tmdb_id = 123
    request_record.media_type = MediaType.TV

    async def fake_load(db, req_id):
        return request_record

    monkeypatch.setattr(search_sse, "load_request_or_404", fake_load)

    async def fake_process(
        request, progress_callback=None, search_mode="new", cancellation_check=None
    ):
        assert progress_callback is not None
        assert search_mode == "new"
        assert callable(cancellation_check)
        await progress_callback(
            {
                "phase": "starting",
                "percent": 5,
                "message": "Starting TV Search for new for Test Show…",
            }
        )
        return {"status": "staged", "message": "Selected pack"}

    process_search = AsyncMock(side_effect=fake_process)
    monkeypatch.setattr(search_service_mod.SearchService, "process_request_search", process_search)

    response = await search_sse.stream_search_request(request_id=1, db=mock_db)
    chunks = []
    async for chunk in response.body_iterator:
        chunks.append(chunk)
    body = "".join(chunks)

    assert process_search.await_args is not None
    args, kwargs = process_search.await_args
    assert args == (request_record,)
    assert callable(kwargs.get("progress_callback"))
    assert kwargs.get("search_mode") == "new"
    assert "Starting TV Search for new for Test Show" in body
    assert "TV Search for new complete" in body
    assert "applied auto-stage/select rules for actionable episodes" in body
    assert '"reload_details": true' in body
    assert '"buckets_source": "db"' in body


@pytest.mark.asyncio
async def test_stream_search_request_tv_full_search_mode(mock_db, monkeypatch):
    request_record = MagicMock()
    request_record.title = "Test Show"
    request_record.year = 2020
    request_record.tmdb_id = 123
    request_record.media_type = MediaType.TV

    async def fake_load(db, req_id):
        return request_record

    async def fake_process(
        request, progress_callback=None, search_mode="new", cancellation_check=None
    ):
        assert progress_callback is not None
        assert search_mode == "full"
        assert callable(cancellation_check)
        await progress_callback(
            {
                "phase": "broad_tv_pack_search_starting",
                "percent": 79,
                "message": "Running one broad TV pack query for Full search…",
            }
        )
        return {"status": "pending", "message": "Refreshed"}

    monkeypatch.setattr(search_sse, "load_request_or_404", fake_load)
    process_search = AsyncMock(side_effect=fake_process)
    monkeypatch.setattr(search_service_mod.SearchService, "process_request_search", process_search)

    response = await search_sse.stream_search_request(request_id=1, search_mode="full", db=mock_db)
    chunks = []
    async for chunk in response.body_iterator:
        chunks.append(chunk)
    body = "".join(chunks)

    assert process_search.await_args is not None
    assert process_search.await_args.kwargs["search_mode"] == "full"
    assert "Running one broad TV pack query for Full search" in body
    assert "TV Full search complete" in body


@pytest.mark.asyncio
async def test_stream_search_request_continues_after_client_disconnect(mock_db, monkeypatch):
    request_record = MagicMock()
    request_record.title = "Test Movie"
    request_record.year = 2020
    request_record.tmdb_id = 123
    request_record.media_type.value = "movie"

    async def fake_load(db, req_id):
        return request_record

    search_started = asyncio.Event()
    allow_search_finish = asyncio.Event()

    async def fake_process(request, progress_callback=None):
        assert progress_callback is None
        search_started.set()
        await allow_search_finish.wait()
        return {"status": "completed"}

    monkeypatch.setattr(search_sse, "load_request_or_404", fake_load)
    process_search = AsyncMock(side_effect=fake_process)
    monkeypatch.setattr(search_service_mod.SearchService, "process_request_search", process_search)

    generator = search_sse._search_request_generator(1, mock_db)
    first_chunk = await generator.__anext__()
    assert '"phase": "starting"' in first_chunk

    await search_started.wait()
    await generator.aclose()
    allow_search_finish.set()
    await asyncio.sleep(0)

    process_search.assert_awaited_once_with(request_record, progress_callback=None)


@pytest.mark.asyncio
async def test_stream_search_request_tv_streams_progress_callback_events(mock_db, monkeypatch):
    request_record = MagicMock()
    request_record.title = "Test Show"
    request_record.year = 2020
    request_record.tmdb_id = 123
    request_record.media_type = MediaType.TV

    async def fake_load(db, req_id):
        return request_record

    async def fake_process(
        request, progress_callback=None, search_mode="new", cancellation_check=None
    ):
        assert request is request_record
        assert progress_callback is not None
        assert search_mode == "new"
        assert callable(cancellation_check)
        await progress_callback(
            {
                "phase": "season_query",
                "percent": 22,
                "message": "Searching season 1 with one normalized season query…",
                "subtitle": "Page size 100",
            }
        )
        await progress_callback(
            {
                "phase": "season_stop",
                "percent": 45,
                "message": "Stopped season 1: page had no new releases.",
                "detail": "100 unique release(s) kept.",
            }
        )
        return {"status": "staged", "message": "Selected pack"}

    monkeypatch.setattr(search_sse, "load_request_or_404", fake_load)
    monkeypatch.setattr(
        search_service_mod.SearchService,
        "process_request_search",
        AsyncMock(side_effect=fake_process),
    )

    response = await search_sse.stream_search_request(request_id=1, db=mock_db)
    chunks = []
    async for chunk in response.body_iterator:
        chunks.append(chunk)
    body = "".join(chunks)

    assert '"phase": "season_query"' in body
    assert '"percent": 22' in body
    assert "Searching season 1 with one normalized season query" in body
    assert '"subtitle": "Page size 100"' in body
    assert '"phase": "season_stop"' in body
    assert '"detail": "100 unique release(s) kept."' in body
    assert '"phase": "complete"' in body


@pytest.mark.asyncio
async def test_cancel_search_request_sets_active_search_event():
    cancel_event = asyncio.Event()
    search_sse._tv_search_cancel_events[42] = cancel_event
    try:
        result = await search_sse.cancel_search_request(42)
    finally:
        search_sse._tv_search_cancel_events.pop(42, None)

    assert result["cancelled"] is True
    assert cancel_event.is_set()


@pytest.mark.asyncio
async def test_cancel_search_request_rejects_when_search_is_not_active():
    with pytest.raises(search_sse.HTTPException) as exc_info:
        await search_sse.cancel_search_request(999)

    assert exc_info.value.status_code == 409


@pytest.mark.asyncio
async def test_closing_tv_stream_cancels_detached_search(mock_db, monkeypatch):
    request_record = MagicMock(title="Test Show", media_type=MediaType.TV)

    async def fake_load(db, req_id):
        return request_record

    search_stopped = asyncio.Event()

    async def fake_process(
        request, progress_callback=None, search_mode="new", cancellation_check=None
    ):
        assert progress_callback is not None
        assert cancellation_check is not None
        await progress_callback({"phase": "searching", "percent": 10, "message": "Searching"})
        while not cancellation_check():
            await asyncio.sleep(0)
        search_stopped.set()
        return {"status": "pending", "cancelled": True}

    monkeypatch.setattr(search_sse, "load_request_or_404", fake_load)
    monkeypatch.setattr(
        search_service_mod.SearchService,
        "process_request_search",
        AsyncMock(side_effect=fake_process),
    )

    generator = search_sse._search_request_generator(77, mock_db)
    first_chunk = await generator.__anext__()
    assert '"phase": "searching"' in first_chunk
    await generator.aclose()
    await asyncio.wait_for(search_stopped.wait(), timeout=1)
    await asyncio.sleep(0)

    assert 77 not in search_sse._tv_search_cancel_events


@pytest.mark.asyncio
async def test_stream_search_request_handles_exception(mock_db, monkeypatch):
    async def fake_load(db, req_id):
        raise RuntimeError("Prowlarr down")

    monkeypatch.setattr(search_sse, "load_request_or_404", fake_load)

    response = await search_sse.stream_search_request(request_id=1, db=mock_db)
    chunks = []
    async for chunk in response.body_iterator:
        chunks.append(chunk)
    body = "".join(chunks)

    assert '"phase": "error"' in body
    assert '"message": "Prowlarr down"' in body


@pytest.mark.asyncio
async def test_stream_bulk_search_yields_progress_and_results(mock_db, monkeypatch):
    requests_by_id = {
        1: MagicMock(id=1, title="A"),
        2: MagicMock(id=2, title="B"),
    }

    async def fake_load(db, req_id):
        return requests_by_id[req_id]

    monkeypatch.setattr(search_sse, "load_request_or_404", fake_load)

    process_search = AsyncMock(return_value={"status": "completed"})
    monkeypatch.setattr(search_service_mod.SearchService, "process_request_search", process_search)

    response = await search_sse.stream_bulk_search(request_ids=[1, 2], db=mock_db)
    chunks = []
    async for chunk in response.body_iterator:
        chunks.append(chunk)
    body = "".join(chunks)

    assert '"phase": "starting"' in body
    assert '"total": 2' in body
    assert body.count('"phase": "searching"') == 2
    assert '"current": 1' in body
    assert '"current": 2' in body
    assert '"phase": "complete"' in body
    assert '"results"' in body
    assert '"request_id": 1' in body
    assert '"request_id": 2' in body
    assert '"title": "A"' in body
    assert '"title": "B"' in body


@pytest.mark.asyncio
async def test_stream_bulk_search_all_pending_loads_server_side(mock_db, monkeypatch):
    request_records = [MagicMock(id=10, title="Hidden"), MagicMock(id=11, title="Visible")]

    load_all_pending = AsyncMock(return_value=request_records)
    monkeypatch.setattr(search_sse, "_load_all_pending_search_requests", load_all_pending)

    load_request = AsyncMock()
    monkeypatch.setattr(search_sse, "load_request_or_404", load_request)

    process_search = AsyncMock(return_value={"status": "completed"})
    monkeypatch.setattr(search_service_mod.SearchService, "process_request_search", process_search)

    response = await search_sse.stream_bulk_search(
        request_ids=[], search_all_pending=True, db=mock_db
    )
    chunks = []
    async for chunk in response.body_iterator:
        chunks.append(chunk)
    body = "".join(chunks)

    load_all_pending.assert_awaited_once_with(mock_db)
    load_request.assert_not_awaited()
    process_search.assert_any_await(request_records[0])
    process_search.assert_any_await(request_records[1])
    assert '"total": 2' in body
    assert '"request_id": 10' in body
    assert '"request_id": 11' in body


@pytest.mark.asyncio
async def test_stream_bulk_search_reports_request_failure_and_continues(mock_db, monkeypatch):
    requests_by_id = {
        1: MagicMock(id=1, title="Fails"),
        2: MagicMock(id=2, title="Continues"),
    }

    async def fake_load(db, req_id):
        return requests_by_id[req_id]

    monkeypatch.setattr(search_sse, "load_request_or_404", fake_load)

    process_search = AsyncMock(side_effect=[RuntimeError("Indexer down"), {"status": "completed"}])
    monkeypatch.setattr(search_service_mod.SearchService, "process_request_search", process_search)

    response = await search_sse.stream_bulk_search(request_ids=[1, 2], db=mock_db)
    chunks = []
    async for chunk in response.body_iterator:
        chunks.append(chunk)
    body = "".join(chunks)

    assert '"phase": "error"' not in body
    assert '"phase": "complete"' in body
    assert '"request_id": 1' in body
    assert '"status": "failed"' in body
    assert '"message": "Indexer down"' in body
    assert '"request_id": 2' in body
    assert process_search.await_count == 2


@pytest.mark.asyncio
async def test_stream_tv_season_pack_search(mock_db, monkeypatch):
    """Granular season-pack stream remains available for compatibility/debug."""
    request_record = MagicMock()
    request_record.media_type = MediaType.TV
    request_record.tvdb_id = 999
    request_record.title = "Show"

    async def fake_load(db, req_id):
        return request_record

    monkeypatch.setattr(search_sse, "load_request_or_404", fake_load)

    fake_service = MagicMock()
    fake_service.search_season_packs = AsyncMock(return_value=MagicMock())
    monkeypatch.setattr(search_sse, "SearchService", lambda db: fake_service)
    monkeypatch.setattr(
        search_sse,
        "serialize_tv_search_response",
        lambda data: {
            "releases": [{"title": "Pack"}],
            "scope": {"type": "season_packs", "season_number": 1},
        },
    )

    response = await search_sse.stream_tv_season_pack_search(
        request_id=1, season_number=1, db=mock_db
    )
    chunks = []
    async for chunk in response.body_iterator:
        chunks.append(chunk)
    body = "".join(chunks)

    assert '"phase": "starting"' in body
    assert '"phase": "searching"' in body
    assert '"phase": "complete"' in body
    assert '"title": "Pack"' in body
    assert '"type": "season_packs"' in body


@pytest.mark.asyncio
async def test_stream_tv_multi_season_search(mock_db, monkeypatch):
    """Granular multi-season stream remains available for compatibility/debug."""
    request_record = MagicMock()
    request_record.media_type = MediaType.TV
    request_record.tvdb_id = 999
    request_record.title = "Show"

    async def fake_load(db, req_id):
        return request_record

    monkeypatch.setattr(search_sse, "load_request_or_404", fake_load)

    fake_service = MagicMock()
    fake_service.search_multi_season_packs = AsyncMock(return_value=MagicMock())
    monkeypatch.setattr(search_sse, "SearchService", lambda db: fake_service)
    monkeypatch.setattr(
        search_sse,
        "serialize_tv_search_response",
        lambda data: {
            "releases": [{"title": "Multi-Pack"}],
            "scope": {"type": "multi_season_packs"},
            "known_total_seasons": 3,
        },
    )

    response = await search_sse.stream_tv_multi_season_search(request_id=1, db=mock_db)
    chunks = []
    async for chunk in response.body_iterator:
        chunks.append(chunk)
    body = "".join(chunks)

    assert '"phase": "starting"' in body
    assert '"phase": "searching"' in body
    assert '"phase": "complete"' in body
    assert '"title": "Multi-Pack"' in body
    assert '"type": "multi_season_packs"' in body
    assert '"known_total_seasons": 3' in body


@pytest.mark.asyncio
async def test_stream_tv_episode_search(mock_db, monkeypatch):
    """Granular episode stream remains available for compatibility/debug."""
    request_record = MagicMock()
    request_record.media_type = MediaType.TV
    request_record.tvdb_id = 999
    request_record.title = "Show"

    async def fake_load(db, req_id):
        return request_record

    monkeypatch.setattr(search_sse, "load_request_or_404", fake_load)

    fake_service = MagicMock()
    fake_service.search_episode = AsyncMock(return_value=MagicMock())
    monkeypatch.setattr(search_sse, "SearchService", lambda db: fake_service)
    monkeypatch.setattr(
        search_sse,
        "serialize_tv_search_response",
        lambda data: {
            "releases": [{"title": "Episode"}],
            "scope": {"type": "single_episode", "season_number": 1, "episode_number": 2},
        },
    )

    response = await search_sse.stream_tv_episode_search(
        request_id=1, season_number=1, episode_number=2, db=mock_db
    )
    chunks = []
    async for chunk in response.body_iterator:
        chunks.append(chunk)
    body = "".join(chunks)

    assert '"phase": "starting"' in body
    assert '"phase": "searching"' in body
    assert '"phase": "complete"' in body
    assert '"title": "Episode"' in body
    assert '"type": "single_episode"' in body


@pytest.mark.asyncio
async def test_tv_pack_streams_use_a_detached_session(mock_db, monkeypatch):
    """StreamingResponse bodies run after ``Depends(get_db)`` closes its session.

    Using that session inside the generator raises ``greenlet_spawn has not
    been called``, so the TV pack/episode streams must open their own session.
    """
    request_record = MagicMock()
    request_record.media_type = MediaType.TV
    request_record.tvdb_id = 999
    request_record.title = "Show"

    detached_session = MagicMock(name="detached-session")
    detached_session.commit = AsyncMock()
    detached_session.__aenter__ = AsyncMock(return_value=detached_session)
    detached_session.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(
        search_sse.database, "async_session_maker", MagicMock(return_value=detached_session)
    )

    used_sessions = []

    async def fake_load(db, req_id):
        used_sessions.append(db)
        return request_record

    monkeypatch.setattr(search_sse, "load_request_or_404", fake_load)

    fake_service = MagicMock()
    fake_service.search_multi_season_packs = AsyncMock(return_value=MagicMock())
    fake_service.search_season_packs = AsyncMock(return_value=MagicMock())
    fake_service.search_episode = AsyncMock(return_value=MagicMock())

    def capture_service(db):
        used_sessions.append(db)
        return fake_service

    monkeypatch.setattr(search_sse, "SearchService", capture_service)
    monkeypatch.setattr(
        search_sse,
        "serialize_tv_search_response",
        lambda data: {"releases": [], "scope": {"type": "multi_season_packs"}},
    )

    streams = [
        await search_sse.stream_tv_multi_season_search(request_id=1, db=mock_db),
        await search_sse.stream_tv_season_pack_search(request_id=1, season_number=2, db=mock_db),
        await search_sse.stream_tv_episode_search(
            request_id=1, season_number=2, episode_number=3, db=mock_db
        ),
    ]
    for response in streams:
        body = "".join([chunk async for chunk in response.body_iterator])
        assert '"phase": "complete"' in body

    assert used_sessions
    assert all(session is detached_session for session in used_sessions)
    assert mock_db not in used_sessions
