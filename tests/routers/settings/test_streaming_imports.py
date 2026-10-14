"""Settings streaming and import flow tests."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from sqlalchemy import select

from app.siftarr.models.activity_log import ActivityLog
from app.siftarr.models.episode import Episode
from app.siftarr.models.request import MediaType, RequestStatus
from app.siftarr.models.request import Request as RequestModel
from app.siftarr.models.season import Season
from app.siftarr.routers import settings
from app.siftarr.services.admin import settings_service
from app.siftarr.services.integrations.plex_service import PlexService
from tests.factories import make_request

import app.siftarr.services.lifecycle.episode_sync_service as episode_sync_module  # isort: skip


async def collect_events(stream) -> list[dict[str, Any]]:
    """Drain an SSE generator and return the decoded ``data:`` payloads."""
    events: list[dict[str, Any]] = []
    async for chunk in stream:
        for line in chunk.splitlines():
            if line.startswith("data: "):
                events.append(json.loads(line.removeprefix("data: ")))
    return events


class FakeSessionContext:
    async def __aenter__(self):
        return MagicMock()

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _NoopPlexService:
    def __init__(self, settings=None):
        self.settings = settings


async def _noop(*_args, **_kwargs):
    return None


class FakeOverseerrBase:
    """Seerr client stand-in; subclasses set ``requests``, ``details`` and ``seasons``."""

    requests: list[dict[str, Any]] = []
    details: dict[int, dict[str, Any]] = {}
    deleted: frozenset[int] = frozenset()
    season_detail_calls: list[int]

    def __init__(self, settings=None):
        self.settings = settings

    async def get_all_requests(self, status=None):
        assert status is None
        return self.requests

    async def request_exists(self, request_id):
        return False if request_id in self.deleted else None

    def normalize_request_status(self, status):
        return str(status).lower()

    def normalize_media_status(self, status):
        return str(status).lower() if status is not None else "unknown"

    async def get_media_details(self, media_type, external_id):
        return self.details[external_id]

    async def close(self):
        return None


def fake_overseerr(requests, *, details=None, deleted=frozenset(), **attrs):
    return type(
        "FakeOverseerr",
        (FakeOverseerrBase,),
        {"requests": requests, "details": details or {}, "deleted": deleted, **attrs},
    )


async def _run_import(db, requests, *, deleted=frozenset(), prepare=_noop):
    """Run an import. IDs in ``deleted`` return 404 upstream; others are unconfirmed."""
    with patch.multiple(
        settings_service,
        OverseerrService=fake_overseerr(requests, deleted=deleted),
        PlexService=_NoopPlexService,
        evaluate_imported_request=_noop,
        prepare_overseerr_import=prepare,
    ):
        return await settings_service.import_overseerr_requests(
            db, MagicMock(overseerr_sync_concurrency=1)
        )


async def _by_external_id(db) -> dict[str, RequestModel]:
    rows = (await db.execute(select(RequestModel))).scalars().all()
    return {row.external_id: row for row in rows}


async def _activity_count(db) -> int:
    return len((await db.execute(select(ActivityLog))).scalars().all())


@pytest.mark.asyncio
async def test_overseerr_import_marks_missing_upstream_linked_requests_denied(db):
    retry = datetime.now(UTC)
    await make_request(
        db, external_id="10", overseerr_request_id=10, next_retry_at=retry, retry_count=2
    )
    await make_request(
        db, external_id="20", overseerr_request_id=20, next_retry_at=retry, retry_count=1
    )
    await make_request(db, external_id="30", next_retry_at=retry, retry_count=3)
    await db.commit()

    upstream = [{"id": 20, "status": "approved", "media": {"tmdbId": 20, "mediaType": "movie"}}]
    for _ in range(2):  # a repeated sync must not log the cleanup twice
        await _run_import(db, upstream, deleted=frozenset({10}))

    rows = await _by_external_id(db)
    assert rows["10"].status == RequestStatus.DENIED
    assert rows["10"].overseerr_request_id == 10
    assert rows["10"].next_retry_at is None
    assert rows["10"].retry_count == 0
    assert rows["20"].status == RequestStatus.PENDING
    assert rows["20"].next_retry_at is not None
    assert rows["30"].status == RequestStatus.PENDING
    assert rows["30"].overseerr_request_id is None
    assert rows["30"].next_retry_at is not None
    assert await _activity_count(db) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "upstream_deleted", "expected"),
    [
        (RequestStatus.PENDING, True, RequestStatus.DENIED),
        (RequestStatus.FAILED, True, RequestStatus.DENIED),
        # Absent from the list but not confirmed deleted: keep it.
        (RequestStatus.PENDING, False, RequestStatus.PENDING),
        # Terminal and in-progress workflow states are never rewritten.
        (RequestStatus.COMPLETED, True, RequestStatus.COMPLETED),
        (RequestStatus.DENIED, True, RequestStatus.DENIED),
        (RequestStatus.STAGED, True, RequestStatus.STAGED),
        (RequestStatus.DOWNLOADING, True, RequestStatus.DOWNLOADING),
        (RequestStatus.UNRELEASED, True, RequestStatus.UNRELEASED),
    ],
)
async def test_overseerr_import_empty_upstream_response_cleans_linked_only(
    db, status, upstream_deleted, expected
):
    retry = datetime.now(UTC)
    linked = await make_request(
        db, external_id="11", status=status, overseerr_request_id=11, next_retry_at=retry
    )
    await make_request(db, external_id="12", next_retry_at=retry)
    await db.commit()

    deleted = frozenset({11}) if upstream_deleted else frozenset()
    assert await _run_import(db, [], deleted=deleted) == (0, 0)

    rows = await _by_external_id(db)
    assert rows["11"].status == expected
    assert (rows["11"].next_retry_at is None) == (expected != status)
    assert rows["12"].status == RequestStatus.PENDING
    assert rows["12"].next_retry_at is not None
    assert await _activity_count(db) == (1 if expected != status else 0)
    del linked


@pytest.mark.asyncio
async def test_overseerr_import_does_not_reimport_request_after_stale_cleanup(db):
    """A request denied by stale cleanup must not come back as a duplicate row."""

    async def prepare(ov_req, *_args, **_kwargs):
        return settings_service.PreparedOverseerrImport(
            external_id="66134",
            media_type=MediaType.TV,
            tmdb_id=None,
            tvdb_id=66134,
            title="Show",
            year=None,
            requester_username=None,
            requester_email=None,
            overseerr_request_id=ov_req["id"],
            media_details=None,
        )

    await make_request(db, external_id="66134", media_type=MediaType.TV, overseerr_request_id=102)
    await db.commit()

    await _run_import(db, [], deleted=frozenset({102}))
    upstream = [{"id": 102, "status": "approved", "media": {"status": "pending"}}]
    assert await _run_import(db, upstream, prepare=prepare) == (0, 1)

    assert len(await _by_external_id(db)) == 1


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
        settings_service.run_bounded_with_progress(
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
    monkeypatch.setattr(
        settings_service,
        "get_settings",
        lambda: MagicMock(
            overseerr_url="http://overseerr",
            overseerr_api_key="key",
            overseerr_sync_concurrency=2,
        ),
    )
    monkeypatch.setattr(settings_service.db_mod, "async_session_maker", FakeSessionContext)

    async def import_overseerr_requests(_db, _runtime_settings, **kwargs):
        emit = kwargs["on_event"]
        stages = [
            ("fetching", 0, 4, "Fetching requests from Overseerr...", []),
            ("prefetching", 1, 3, "Fetching metadata for Movie One...", ["Movie One", "Show One"]),
            ("importing", 1, 2, "Importing Show One...", ["Show One"]),
            ("episode_sync", 0, 1, "Syncing TV episodes for Show One...", ["Show One"]),
        ]
        for phase, current, total, message, active in stages:
            await emit(
                settings_service.build_sse_progress(
                    phase, current=current, total=total, message=message, active=active
                )
            )
        return 2, 1

    monkeypatch.setattr(settings_service, "import_overseerr_requests", import_overseerr_requests)
    events = await collect_events(settings_service.sync_overseerr_generator())

    assert [event["phase"] for event in events] == [
        "connecting",
        "fetching",
        "prefetching",
        "importing",
        "episode_sync",
        "complete",
    ]
    for event in events[1:-1]:
        assert event["current"] < event["total"]
        assert event["message"]
        assert isinstance(event["active"], list)
    assert events[-1]["message"] == "Synced 2 new request(s) from Overseerr"


def _plex_request(request_id, title, media_type, status=RequestStatus.PENDING, seasons=()):
    request = MagicMock(id=request_id, media_type=media_type, status=status)
    request.title = title
    request.seasons = list(seasons)
    return request


@pytest.fixture
def plex_rescan(monkeypatch):
    """Run ``rescan_plex_generator`` against a scripted polling service.

    Returns ``(run, tv_rescan, polling)``; ``run(shallow)`` yields the decoded SSE events.
    """
    monkeypatch.setattr(
        settings_service, "get_settings", lambda: MagicMock(plex_sync_concurrency=2)
    )
    polling = AsyncMock()
    monkeypatch.setattr(settings_service, "PlexPollingService", lambda db, plex: polling)
    tv_rescan = AsyncMock(return_value=True)
    monkeypatch.setattr(settings_service, "rescan_plex_tv_request", tv_rescan)
    monkeypatch.setattr(settings_service, "PlexService", lambda settings: AsyncMock())
    monkeypatch.setattr(settings_service.db_mod, "async_session_maker", FakeSessionContext)

    async def run(shallow: bool) -> list[dict[str, Any]]:
        return await collect_events(settings_service.rescan_plex_generator(shallow=shallow))

    return run, tv_rescan, polling


@pytest.mark.asyncio
async def test_rescan_plex_partial_scans_recent_without_tv_resync(plex_rescan):
    run, tv_rescan, polling = plex_rescan
    scan_result = MagicMock(
        metrics=MagicMock(scanned_items=10, matched_requests=3, skipped_on_error_items=1),
        completed_requests=2,
        last_error=None,
    )
    polling.scan_recent = AsyncMock(return_value=scan_result)

    events = await run(True)

    assert [event["phase"] for event in events][0] == "connecting"
    assert all(event["mode"] == "partial" for event in events if "mode" in event)
    assert events[-1]["phase"] == "complete"
    assert events[-1]["completed"] == 2
    polling.scan_recent.assert_awaited_once()
    tv_rescan.assert_not_called()


@pytest.mark.asyncio
async def test_rescan_plex_full_resyncs_non_completed_tv_and_reports_weighted_progress(
    plex_rescan,
):
    run, tv_rescan, polling = plex_rescan
    all_completed = _plex_request(
        11,
        "Complete Episodes Show",
        MediaType.TV,
        seasons=[MagicMock(episodes=[MagicMock(status=RequestStatus.COMPLETED)])],
    )
    polling.get_active_requests = AsyncMock(
        return_value=[
            _plex_request(1, "Movie", MediaType.MOVIE),
            _plex_request(10, "Active Show", MediaType.TV),
            all_completed,
            _plex_request(12, "Done", MediaType.TV, status=RequestStatus.COMPLETED),
        ]
    )

    async def poll(on_progress):
        await on_progress(
            {
                "phase": "poll",
                "current": 0,
                "total": 2,
                "completed": 0,
                "title": "Show",
                "active": ["Show"],
                "detail": "tv_episode_availability",
                "item_progress": 0.6,
            }
        )
        await on_progress({"phase": "poll", "current": 2, "total": 2, "completed": 2, "active": []})
        return 2

    polling.poll = AsyncMock(side_effect=poll)

    events = await run(False)

    assert [call.args[0] for call in tv_rescan.await_args_list] == [10, 11]
    fetching = next(event for event in events if event["phase"] == "fetching")
    assert fetching["mode"] == "full"
    assert fetching["active"] == ["Movie", "Active Show", "Complete Episodes Show"]
    assert events[-1]["phase"] == "complete"
    assert events[-1]["completed"] == 2

    percents = [event["overall_percent"] for event in events if "overall_percent" in event]
    assert percents == sorted(percents)
    assert all(percent < 100 for percent in percents)


@pytest.mark.asyncio
async def test_full_rescan_sse_rejects_overlapping_full_sync():
    """A second full-sync SSE stream should not start overlapping full-sync work."""

    await settings_service._FULL_PLEX_SYNC_LOCK.acquire()
    try:
        events = await collect_events(settings_service.rescan_plex_generator(shallow=False))
    finally:
        settings_service._FULL_PLEX_SYNC_LOCK.release()

    assert [event["phase"] for event in events] == ["connecting", "locked"]


@pytest.fixture
def seerr_sync(monkeypatch, base_context):
    """Patch the settings router collaborators used by ``sync_overseerr``.

    Returns a ``run(db)`` coroutine function that returns the rendered page context.
    """
    env = {"overseerr_url": "http://ov", "overseerr_api_key": "key"}
    runtime = MagicMock(
        overseerr_sync_concurrency=2,
        plex_sync_concurrency=2,
        plex_url="http://plex:32400",
        plex_token="token",
    )
    store = MagicMock(get_effective_dict=AsyncMock(return_value=env))
    monkeypatch.setattr(
        settings,
        "_build_settings_page_context",
        AsyncMock(return_value={**base_context(), "env": env}),
    )
    monkeypatch.setattr(settings, "get_settings", lambda: runtime)
    monkeypatch.setattr(settings, "SettingsStore", lambda db: store)
    monkeypatch.setattr(settings_service, "evaluate_imported_request", AsyncMock(return_value=None))
    monkeypatch.setattr(settings_service, "PlexService", lambda settings: AsyncMock())

    async def run(db) -> dict[str, Any]:
        response = await settings.sync_overseerr(MagicMock(), db=db)
        return cast(dict, getattr(response, "context", None))

    return run


def _tv_request(request_id=101, tmdb_id=2):
    return {
        "id": request_id,
        "status": "approved",
        "media": {"tmdbId": tmdb_id, "mediaType": "tv"},
        "requestedBy": {"username": "tv-user"},
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("existing_external_ids", "expected_external_id"),
    [(["1"], "1-201"), (["1", "1-201"], "1-201-2")],
)
async def test_sync_overseerr_uses_available_request_id_when_external_id_collides(
    monkeypatch, seerr_sync, db, existing_external_ids, expected_external_id
):
    """A new Seerr request must not disappear behind an ambiguous external ID."""
    for external_id in existing_external_ids:
        await make_request(db, external_id=external_id, tmdb_id=None)
    await make_request(
        db, external_id="999", media_type=MediaType.TV, tvdb_id=None, overseerr_request_id=202
    )
    await db.commit()
    requests = [
        {"id": 201, "status": "approved", "media": {"tmdbId": 1, "mediaType": "movie"}},
        {"id": 202, "status": "pending", "media": {"tmdbId": 999, "mediaType": "tv"}},
    ]
    details = {i: {"title": f"Title {i}", "status": "Released"} for i in (1, 999)}
    monkeypatch.setattr(
        settings_service, "OverseerrService", fake_overseerr(requests, details=details)
    )

    context = await seerr_sync(db)

    assert context["message_type"] == "success"
    assert context["message"] == "Synced 1 new request(s) from Overseerr"
    created = (
        (await db.execute(select(RequestModel).where(RequestModel.overseerr_request_id == 201)))
        .scalars()
        .all()
    )
    assert [row.external_id for row in created] == [expected_external_id]


DEGRADED_WARNING = (
    "EpisodeSyncService: degraded Plex sync for request 1 (Show One); "
    "Plex episode availability was inconclusive, preserving existing episode/request state"
)


@pytest.mark.asyncio
async def test_sync_overseerr_logs_request_level_degraded_tv_sync_once(
    monkeypatch, seerr_sync, db, caplog
):
    """Overseerr import should surface one request-level degraded sync warning."""
    details = {2: {"name": "Show One", "firstAirDate": "2023-02-03", "status": "Returning Series"}}
    monkeypatch.setattr(
        settings_service, "OverseerrService", fake_overseerr([_tv_request()], details=details)
    )

    class FakeEpisodeSyncService:
        def __init__(self, db, overseerr=None, plex=None):
            pass

        async def sync_request(self, request_id):
            settings.logger.warning(DEGRADED_WARNING.replace("request 1", f"request {request_id}"))

    monkeypatch.setattr(episode_sync_module, "EpisodeSyncService", FakeEpisodeSyncService)

    context = await seerr_sync(db)

    assert context["message"] == "Synced 1 new request(s) from Overseerr"
    assert [r.message for r in caplog.records if "degraded Plex sync" in r.message] == [
        DEGRADED_WARNING
    ]


@pytest.mark.asyncio
async def test_sync_overseerr_fresh_tv_import_bounds_work_and_preserves_state_on_transient_plex_failure(
    monkeypatch, seerr_sync, db, caplog
):
    """Fresh TV import should fail fast on Plex ReadError without per-season spam."""
    future_day = (datetime.now(UTC) + timedelta(days=7)).date().isoformat()
    details = {
        2: {
            "name": "Show One",
            "firstAirDate": "2023-02-03",
            "status": "Returning Series",
            "seasons": [{"seasonNumber": n, "name": f"Season {n}"} for n in (1, 2, 3)],
        }
    }

    class SeasonAwareOverseerr(fake_overseerr([_tv_request()], details=details)):
        season_detail_calls: list[int] = []

        async def get_season_details(self, external_id, season_number):
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

    monkeypatch.setattr(settings_service, "OverseerrService", SeasonAwareOverseerr)

    runtime = settings.get_settings()
    plex_service = PlexService(settings=runtime)
    season_1_cancelled = asyncio.Event()
    plex_call_keys: list[str] = []

    def plex_response(metadata):
        response = MagicMock(status_code=200)
        response.json.return_value = {"MediaContainer": {"Metadata": metadata}}
        return response

    async def plex_get(url: str, **_kwargs):
        rating_key = url.rstrip("/").split("/")[-2]
        plex_call_keys.append(rating_key)
        if rating_key == "show-123":
            return plex_response(
                [{"type": "season", "index": n, "ratingKey": f"season-{n}"} for n in (1, 2, 3)]
            )
        if rating_key == "season-1":
            try:
                await asyncio.Future[None]()
            except asyncio.CancelledError:
                season_1_cancelled.set()
                raise
        if rating_key == "season-2":
            raise httpx.ReadError("boom", request=httpx.Request("GET", url))
        return plex_response([{"type": "episode", "index": 1, "Media": [{"id": 1}]}])

    mock_client = AsyncMock()
    mock_client.get.side_effect = plex_get
    monkeypatch.setattr(
        plex_service, "resolve_show_rating_key", AsyncMock(return_value=("show-123", True))
    )
    monkeypatch.setattr(plex_service, "_get_client", AsyncMock(return_value=mock_client))
    monkeypatch.setattr(settings_service, "PlexService", lambda settings: plex_service)

    context = await seerr_sync(db)

    assert context["message"] == "Synced 1 new request(s) from Overseerr"
    assert SeasonAwareOverseerr.season_detail_calls == [1, 2, 3]
    await asyncio.wait_for(season_1_cancelled.wait(), timeout=1)
    # The ReadError on season 2 stops the work: season 3 is never requested.
    assert plex_call_keys == ["show-123", "season-1", "season-2"]

    tv_request = (await db.execute(select(RequestModel))).scalar_one()
    seasons = (await db.execute(select(Season).order_by(Season.season_number))).scalars().all()
    episodes = (await db.execute(select(Episode).order_by(Episode.season_id))).scalars().all()
    expected = [RequestStatus.PENDING, RequestStatus.UNRELEASED, RequestStatus.PENDING]
    assert [season.status for season in seasons] == expected
    assert [episode.status for episode in episodes] == expected
    assert tv_request.status == RequestStatus.PENDING
    assert [r.message for r in caplog.records if "inconclusive" in r.message] == [DEGRADED_WARNING]
    assert not [r for r in caplog.records if "_children(" in r.message]
