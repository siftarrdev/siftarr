"""Tests for staged torrent approval, discard, replace and check-now routes.

Preflight warnings (rules, seeders) are covered in tests/test_approval_validation.py.
These tests use a real in-memory database and fake only qBittorrent and the
decision logs.
"""

import itertools
import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.siftarr.config import Settings, get_settings
from app.siftarr.models import Episode, Request, Season, StagedTorrent
from app.siftarr.models.request import MediaType, RequestStatus
from app.siftarr.routers import staged
from app.siftarr.services.integrations.qbittorrent_service import BulkAddResult
from tests.factories import make_episode, make_request, make_season, make_staged_torrent

HASH = "a" * 40
JSON_HEADERS = {"accept": "application/json"}
_counter = itertools.count(1)


@pytest.fixture(autouse=True)
def _settings_env(monkeypatch):
    """Release parsing reads the global cached settings."""
    monkeypatch.setenv("SECRET_KEY", "test-only")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def log_decision(monkeypatch):
    mock = MagicMock()
    monkeypatch.setattr(staged, "log_staging_decision", mock)
    return mock


@pytest.fixture(autouse=True)
def discard_log(monkeypatch):
    mock = MagicMock()
    monkeypatch.setattr(staged, "log_manual_discard_decision", mock)
    monkeypatch.setattr(staged, "log_replacement_decision", MagicMock())
    return mock


@pytest.fixture(autouse=True)
def overseerr(monkeypatch):
    mock = AsyncMock()
    monkeypatch.setattr(staged, "approve_overseerr_request_best_effort", mock)
    return mock


@pytest.fixture(autouse=True)
def _no_local_files(monkeypatch):
    monkeypatch.setattr(staged.os.path, "exists", MagicMock(return_value=False))


@pytest.fixture
def qbit(monkeypatch):
    """qBit fake that accepts every submission and knows no existing torrents."""
    service = AsyncMock()
    service.add_torrent.return_value = HASH
    service.get_torrent_info.return_value = None
    service.get_torrent_info_by_name.return_value = None

    async def add_bulk(payloads):
        return [BulkAddResult(key=p.key, success=True, torrent_hash=HASH) for p in payloads]

    service.add_torrents_bulk.side_effect = add_bulk
    settings = Settings(secret_key="test-only")
    monkeypatch.setattr(staged, "get_settings", lambda: settings)
    monkeypatch.setattr(staged, "QbittorrentService", MagicMock(return_value=service))
    return service


async def stage(
    db,
    *,
    media_type=MediaType.MOVIE,
    request=None,
    request_status=RequestStatus.STAGED,
    **torrent_overrides,
):
    """Persist a request (unless given) and one staged torrent for it."""
    n = next(_counter)
    if request is None:
        request = await make_request(
            db, media_type=media_type, external_id=f"ext-{n}", status=request_status
        )
    overrides = {
        "title": f"Release {n}",
        "magnet_url": f"magnet:?xt=urn:btih:{n:040x}",
    } | torrent_overrides
    torrent = await make_staged_torrent(db, request, **overrides)
    await db.commit()
    return request, torrent


async def approve(db, torrent_id, *, accept_json=True):
    return await staged.approve_staged_torrent(
        torrent_id,
        http_request=MagicMock(headers=JSON_HEADERS if accept_json else {}),
        db=db,
        confirm_identity=True,
        confirm_rules=True,
        confirm_seeders=True,
        confirm_rejected=True,
    )


async def bulk(db, action, ids):
    response = await staged.bulk_staged_action(
        action=action,
        torrent_ids=list(ids),
        http_request=MagicMock(headers=JSON_HEADERS),
        db=db,
        confirm_identity=True,
        confirm_rules=True,
        confirm_seeders=True,
        confirm_rejected=True,
    )
    return response, json.loads(bytes(response.body))  # type: ignore[arg-type]


@pytest.mark.parametrize("source", ["rule", "manual"])
async def test_approve_logs_decision_against_rule_pick(db, qbit, log_decision, source):
    request, rule_pick = await stage(
        db, media_type=MediaType.TV, selection_source="rule", target_scope="same-episode"
    )
    approved = rule_pick
    if source == "manual":
        _, approved = await stage(
            db, request=request, selection_source="manual", target_scope="same-episode"
        )

    response = await approve(db, approved.id, accept_json=False)

    assert response.status_code == 303
    assert approved.status == "approved"
    log_decision.assert_called_once_with(
        request=request, approved_torrent=approved, rules_selected_torrent=rule_pick
    )


async def test_discard_logs_manual_rejection(db, discard_log):
    request, torrent = await stage(db)

    response = await staged.discard_staged_torrent(
        torrent.id, http_request=MagicMock(headers={}), db=db
    )

    assert response.status_code == 303
    assert torrent.status == "discarded"
    discard_log.assert_called_once_with(request=request, rejected_torrent=torrent)


@pytest.mark.parametrize(
    ("qbit_response", "expected_hash"),
    [
        pytest.param(HASH, HASH, id="hash"),
        pytest.param("Ok.", None, id="ok-before-visibility"),
        pytest.param(
            {
                "added_torrent_ids": [],
                "failure_count": 0,
                "pending_count": 1,
                "success_count": 0,
            },
            None,
            id="metadata-pending",
        ),
    ],
)
async def test_approve_accepts_qbit_submission_before_visibility(
    db, qbit, qbit_response, expected_hash
):
    request, torrent = await stage(db)
    qbit.add_torrent.return_value = qbit_response

    response = await approve(db, torrent.id)

    assert response.status_code == 200
    assert json.loads(bytes(response.body)) == {  # type: ignore[arg-type]
        "status": "ok",
        "message": "Torrent approved successfully",
        "torrent_id": torrent.id,
        "torrent_status": "approved",
        "request_status": RequestStatus.DOWNLOADING.value,
        "refresh": ["staged", "downloading"],
    }
    assert torrent.info_hash == expected_hash
    assert request.status == RequestStatus.DOWNLOADING


async def test_approve_qbit_failure_does_not_commit_local_transition(db, qbit):
    request, torrent = await stage(db)
    qbit.add_torrent.return_value = None

    with pytest.raises(HTTPException) as exc_info:
        await approve(db, torrent.id)

    assert exc_info.value.status_code == 500
    await db.refresh(request)
    await db.refresh(torrent)
    assert request.status == RequestStatus.STAGED
    assert torrent.status == "staged"


async def test_approve_overseerr_sync_failure_does_not_block(db, qbit, overseerr):
    request, torrent = await stage(db, request_status=RequestStatus.STAGED)
    request.overseerr_request_id = 99
    overseerr.side_effect = RuntimeError("overseerr down")

    response = await approve(db, torrent.id)

    assert response.status_code == 200
    overseerr.assert_awaited_once()
    assert torrent.status == "approved"
    assert request.status == RequestStatus.DOWNLOADING


async def test_approve_retry_succeeds_when_existing_torrent_confirmed(db, qbit):
    """Retry after local files are removed is idempotent if qBit has the torrent."""
    _, torrent = await stage(db, magnet_url=None, info_hash=HASH)
    qbit.get_torrent_info.return_value = {"hash": HASH, "name": torrent.title}

    response = await approve(db, torrent.id)

    assert response.status_code == 200
    qbit.add_torrent.assert_not_awaited()
    assert torrent.status == "approved"
    assert torrent.info_hash == HASH


@pytest.mark.parametrize("bulk_approve", [False, True], ids=["single", "bulk"])
async def test_approve_uses_sidecar_download_url_when_file_missing(
    db, qbit, tmp_path, bulk_approve
):
    url = "https://example.test/release.torrent"
    sidecar = tmp_path / "stage.json"
    sidecar.write_text(json.dumps({"release": {"download_url": url}}))
    _, torrent = await stage(db, magnet_url=None, json_path=str(sidecar))

    if bulk_approve:
        _, body = await bulk(db, "approve", [torrent.id])
        assert body["processed"] == 1
        payload = qbit.add_torrents_bulk.await_args.args[0][0]
        assert payload.magnet_uri == url
        assert payload.torrent_path is None
    else:
        await approve(db, torrent.id)
        qbit.add_torrent.assert_awaited_once_with(
            magnet_uri=url, category=staged.MediaCategory.MOVIES
        )
    assert torrent.status == "approved"


async def test_bulk_approve_persists_downloading_for_submitted_torrents(db, session_maker, qbit):
    """Rows must be committed, and a not-yet-visible qBit hash must not fail the item."""
    _, first = await stage(db, media_type=MediaType.TV)
    _, second = await stage(db, media_type=MediaType.TV)
    qbit.add_torrents_bulk.side_effect = None
    qbit.add_torrents_bulk.return_value = [
        BulkAddResult(key=first.id, success=True, torrent_hash=HASH),
        BulkAddResult(key=second.id, success=True, torrent_hash=None),
    ]

    response, body = await bulk(db, "approve", [first.id, second.id])

    assert response.status_code == 200
    assert body["status"] == "ok"
    assert body["processed"] == 2
    assert body["failed"] == []
    async with session_maker() as fresh:
        requests = (await fresh.execute(select(Request).order_by(Request.id))).scalars().all()
        torrents = (
            (await fresh.execute(select(StagedTorrent).order_by(StagedTorrent.id))).scalars().all()
        )
    assert [r.status for r in requests] == [RequestStatus.DOWNLOADING] * 2
    assert [t.status for t in torrents] == ["approved", "approved"]
    assert [t.info_hash for t in torrents] == [HASH, None]


async def test_bulk_approve_submits_more_than_seventeen_in_one_bulk_call(db, qbit):
    torrents = [(await stage(db))[1] for _ in range(18)]

    _, body = await bulk(db, "approve", [t.id for t in torrents])

    assert body["processed"] == 18
    qbit.add_torrents_bulk.assert_awaited_once()
    qbit.add_torrent.assert_not_awaited()
    assert {t.status for t in torrents} == {"approved"}


async def test_bulk_approve_reports_no_submission_source(db, qbit):
    _, torrent = await stage(db, magnet_url=None)

    _, body = await bulk(db, "approve", [torrent.id])

    assert body["processed"] == 0
    assert body["status"] == "partial"
    assert body["failed"] == [
        {
            "id": torrent.id,
            "title": torrent.title,
            "error": "No local torrent file, magnet URL, or sidecar download URL available",
        }
    ]
    qbit.add_torrents_bulk.assert_not_awaited()
    assert torrent.status == "staged"


async def test_bulk_approve_reports_partial_qbit_failure(db, qbit):
    """Only qBit-accepted rows become approved; failed rows are reported."""
    _, good = await stage(db)
    _, bad = await stage(db, title="Duplicate Missing Release")
    qbit.add_torrents_bulk.side_effect = None
    qbit.add_torrents_bulk.return_value = [
        BulkAddResult(key=good.id, success=True, torrent_hash=HASH),
        BulkAddResult(key=bad.id, success=False),
    ]

    _, body = await bulk(db, "approve", [good.id, bad.id])

    assert body["processed"] == 1
    assert body["failed"] == [{"id": bad.id, "title": "Duplicate Missing Release"}]
    assert "Failed 1" in body["message"]
    assert good.status == "approved"
    assert bad.status == "staged"


async def test_bulk_discard_handles_torrents_without_request(db):
    torrents = [
        await make_staged_torrent(db, None, title=f"Manual {i}", status="staged") for i in range(2)
    ]
    await db.commit()

    response, body = await bulk(db, "discard", [t.id for t in torrents])

    assert response.status_code == 200
    assert body["processed"] == 2
    assert [t.status for t in torrents] == ["discarded", "discarded"]


@pytest.mark.parametrize(
    ("title", "seasons", "expected", "request_downloading", "use_bulk"),
    [
        pytest.param(
            "Show.S01E01.1080p.WEB-DL",
            {1: [RequestStatus.STAGED, RequestStatus.PENDING, RequestStatus.PENDING]},
            {1: [RequestStatus.DOWNLOADING, RequestStatus.PENDING, RequestStatus.PENDING]},
            False,
            False,
            id="single-episode-leaves-rest-of-show",
        ),
        pytest.param(
            "Show.S01.1080p.WEB-DL",
            {1: [RequestStatus.STAGED] * 2, 2: [RequestStatus.PENDING]},
            {1: [RequestStatus.DOWNLOADING] * 2, 2: [RequestStatus.PENDING]},
            None,
            False,
            id="season-pack-covers-only-its-season",
        ),
        pytest.param(
            "Show S01",
            {1: [RequestStatus.STAGED, RequestStatus.PENDING]},
            {1: [RequestStatus.DOWNLOADING] * 2},
            True,
            True,
            id="bulk-season-marks-all-episodes",
        ),
    ],
)
async def test_tv_approval_scopes_episode_status(
    db, qbit, title, seasons, expected, request_downloading, use_bulk
):
    request = await make_request(
        db, media_type=MediaType.TV, external_id="tv", status=RequestStatus.STAGED
    )
    for number, statuses in seasons.items():
        season = await make_season(db, request, number, status=RequestStatus.STAGED)
        for index, status in enumerate(statuses, start=1):
            await make_episode(db, season, index, status=status)
    _, torrent = await stage(db, request=request, title=title)

    if use_bulk:
        await bulk(db, "approve", [torrent.id])
    else:
        await approve(db, torrent.id)

    rows = (
        await db.execute(
            select(Season.season_number, Episode.status)
            .join(Episode, Episode.season_id == Season.id)
            .order_by(Season.season_number, Episode.episode_number)
        )
    ).all()
    actual: dict[int, list[RequestStatus]] = {}
    for season_number, status in rows:
        actual.setdefault(season_number, []).append(status)
    assert actual == expected
    await db.refresh(request)
    if request_downloading is not None:
        assert (request.status == RequestStatus.DOWNLOADING) is request_downloading


@pytest.mark.parametrize(
    ("redirect_to", "expected_location"),
    [
        ("/?tab=downloading", "/?tab=downloading"),
        ("https://evil.example/phish", "/?tab=staged"),
        ("//evil.example/phish", "/?tab=staged"),
        ("http:\\evil.example\\phish", "/?tab=staged"),
    ],
)
async def test_replace_staged_torrent_redirects_only_to_local_urls(
    db, qbit, redirect_to, expected_location
):
    request, old = await stage(
        db,
        media_type=MediaType.TV,
        request_status=RequestStatus.DOWNLOADING,
        status="approved",
        target_scope="same-episode",
    )
    _, new = await stage(db, request=request, target_scope="same-episode")

    response = await staged.replace_staged_torrent(
        torrent_id=new.id,
        reason="Better quality",
        redirect_to=redirect_to,
        db=db,
        confirm_identity=True,
        confirm_rules=True,
        confirm_seeders=True,
        confirm_rejected=True,
    )

    assert response.status_code == 303
    assert response.headers["location"] == expected_location
    assert old.status == "replaced"
    assert new.status == "approved"


async def test_replace_staged_torrent_rejects_approved_id(db):
    """Replace endpoint must receive the staged replacement candidate ID."""
    _, approved = await stage(db, status="approved")

    with pytest.raises(HTTPException) as exc_info:
        await staged.replace_staged_torrent(
            torrent_id=approved.id, reason=None, redirect_to="/?tab=downloading", db=db
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Replacement torrent must be staged"


async def test_reconcile_request_via_plex_propagates_errors(monkeypatch):
    plex_polling = AsyncMock()
    plex_polling.check_request.side_effect = RuntimeError("plex boom")
    monkeypatch.setattr(staged, "PlexService", MagicMock(return_value=AsyncMock()))
    monkeypatch.setattr(staged, "PlexPollingService", MagicMock(return_value=plex_polling))

    with pytest.raises(RuntimeError, match="plex boom"):
        await staged._reconcile_request_via_plex(
            AsyncMock(), request_id=99, title="Test Show S01E01", runtime_settings=MagicMock()
        )


async def test_check_now_does_not_reconcile_incomplete_torrent(db, qbit, monkeypatch):
    _, torrent = await stage(db, magnet_url="magnet:?xt=urn:btih:" + "d" * 40)
    qbit.get_torrent_info.return_value = {"progress": 0.2, "state": "downloading"}
    plex_polling = AsyncMock()
    monkeypatch.setattr(staged, "PlexService", MagicMock(return_value=AsyncMock()))
    monkeypatch.setattr(staged, "PlexPollingService", MagicMock(return_value=plex_polling))

    response = await staged.check_now(torrent_id=torrent.id, db=db)

    body = json.loads(bytes(response.body))  # type: ignore[arg-type]
    assert body["qbit_complete"] is False
    assert body["plex_available"] is False
    plex_polling.check_request.assert_not_awaited()
