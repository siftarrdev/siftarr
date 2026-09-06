"""Real-session preflight coverage, separate from mocked qBit submission unit tests."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.siftarr.config import Settings
from app.siftarr.models import Base, Release, Request, StagedTorrent
from app.siftarr.models.request import MediaType, RequestStatus
from app.siftarr.models.rule import RuleType
from app.siftarr.routers import staged
from app.siftarr.services.decisions.rule_engine import clear_engine_caches
from app.siftarr.services.decisions.rule_service import RuleService
from app.siftarr.services.releases.release_disposition_service import ReleaseDispositionService


@pytest.fixture
async def approval_db(monkeypatch, tmp_path):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    clear_engine_caches()
    settings = Settings(secret_key="test-only")
    monkeypatch.setattr(staged, "get_settings", lambda: settings)
    monkeypatch.setattr(staged, "STAGING_DECISION_LOG_PATH", tmp_path / "decisions.jsonl")
    monkeypatch.setattr(staged, "approve_overseerr_request_best_effort", AsyncMock())
    async with async_sessionmaker(engine, expire_on_commit=False)() as db:
        yield db
    await engine.dispose()
    clear_engine_caches()


async def make_stage(db, *, title="Example Movie 2025 1080p", request=None, source=True):
    if request is None:
        request = Request(
            external_id="approval-test",
            title="Example Movie",
            year=2025,
            media_type=MediaType.MOVIE,
            status=RequestStatus.STAGED,
        )
        db.add(request)
        await db.flush()
    release = Release(
        request_id=request.id,
        title=title,
        size=2 * 1024**3,
        seeders=10,
        leechers=0,
        indexer="test",
        score=100,
        passed_rules=True,
        download_url="https://example.test/torrent",
        magnet_url="magnet:?xt=urn:btih:" + "a" * 40,
    )
    db.add(release)
    await db.flush()
    torrent = StagedTorrent(
        request_id=request.id,
        title=title,
        size=release.size,
        indexer="test",
        score=100,
        status="staged",
        selection_source="rule",
        torrent_path="",
        json_path="",
        original_filename=title,
        magnet_url=release.magnet_url,
        source_release_id=release.id if source else None,
    )
    db.add(torrent)
    await db.commit()
    return request, release, torrent


def http_request():
    return MagicMock(headers={"accept": "application/json"})


def warning_details(error):
    assert isinstance(error.detail, dict)
    return error.detail["warnings"]


async def test_new_rule_rejects_existing_stage_before_qbit_and_requires_specific_confirmation(
    approval_db,
    monkeypatch,
):
    db = approval_db
    _, _, torrent = await make_stage(db)
    await RuleService(db).create_rule("New exclusion", RuleType.EXCLUSION, "1080p")
    qbit = AsyncMock()
    qbit.add_torrent.return_value = "b" * 40
    monkeypatch.setattr(staged, "QbittorrentService", lambda **_: qbit)
    with pytest.raises(HTTPException) as blocked:
        await staged.approve_staged_torrent(torrent.id, http_request(), db=db)
    assert blocked.value.status_code == 409
    assert {item["code"] for item in warning_details(blocked.value)} == {"rules"}
    qbit.add_torrent.assert_not_awaited()
    assert torrent.status == "staged"
    response = await staged.approve_staged_torrent(
        torrent.id,
        http_request(),
        confirm_rules=True,
        db=db,
    )
    assert response.status_code == 200
    qbit.add_torrent.assert_awaited_once()
    assert torrent.rules_override is True
    assert torrent.info_hash == "b" * 40
    assert torrent.rule_evidence_snapshot["passed"] is False
    assert len(torrent.rule_fingerprint) == 64


async def test_stale_seed_observation_requires_seeder_confirmation(approval_db, monkeypatch):
    _, release, torrent = await make_stage(approval_db)
    release.created_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=2)
    await approval_db.commit()
    qbit_factory = MagicMock()
    monkeypatch.setattr(staged, "QbittorrentService", qbit_factory)
    with pytest.raises(HTTPException) as blocked:
        await staged.approve_staged_torrent(torrent.id, http_request(), db=approval_db)
    assert {item["code"] for item in warning_details(blocked.value)} == {"seeders"}
    assert "observed" in warning_details(blocked.value)[0]["message"]
    qbit_factory.assert_not_called()


async def test_bulk_preflights_every_candidate_before_any_qbit_add(approval_db, monkeypatch):
    request, _, first = await make_stage(approval_db)
    _, release, second = await make_stage(approval_db, request=request)
    release.seeders = 0
    await approval_db.commit()
    qbit_factory = MagicMock()
    monkeypatch.setattr(staged, "QbittorrentService", qbit_factory)
    with pytest.raises(HTTPException) as blocked:
        await staged.bulk_staged_action(
            action="approve",
            torrent_ids=[first.id, second.id],
            http_request=http_request(),
            db=approval_db,
        )
    assert blocked.value.status_code == 409
    assert all(item["torrent_id"] == second.id for item in warning_details(blocked.value))
    qbit_factory.return_value.add_torrent.assert_not_called()
    qbit_factory.return_value.add_torrents_bulk.assert_not_called()
    assert first.status == second.status == "staged"


async def test_durable_rejection_cannot_be_overridden_by_rules_confirmation(approval_db):
    request, release, torrent = await make_stage(approval_db)
    await ReleaseDispositionService(approval_db).record_rejection(
        request.id, release, media_type=request.media_type, reason="Wrong payload"
    )
    await approval_db.commit()
    with pytest.raises(HTTPException) as blocked:
        await staged.approve_staged_torrent(
            torrent.id, http_request(), confirm_rules=True, db=approval_db
        )
    assert any(item["code"] == "rejected" for item in warning_details(blocked.value))


async def test_missing_source_is_explicit_legacy_warning_and_missing_request_fails_closed(
    approval_db,
):
    request, _, torrent = await make_stage(approval_db, source=False)
    warnings = await staged._approval_warnings(approval_db, torrent, request)
    assert {item["code"] for item in warnings} == {"rules", "seeders"}
    with pytest.raises(HTTPException):
        await staged._approval_warnings(approval_db, torrent, None)
    assert staged._confirmation_given(False) is False
    assert staged._confirmation_given(object()) is False


async def test_legacy_scope_derivation_distinguishes_episodes_and_pack_coverage():
    first = StagedTorrent(title="Example Show S01E01", target_scope=None)
    second = StagedTorrent(title="Example Show S01E02", target_scope=None)
    pack = StagedTorrent(title="Example Show S01", target_scope=None)
    multi = StagedTorrent(title="Example Show S01E01E02", target_scope=None)
    equivalent = StagedTorrent(title="Example Show S01E01-E02", target_scope=None)
    assert staged._scope_for(first, MediaType.TV) != staged._scope_for(second, MediaType.TV)
    assert staged._scope_for(first, MediaType.TV) != staged._scope_for(pack, MediaType.TV)
    assert staged._scope_for(first, MediaType.TV) != staged._scope_for(multi, MediaType.TV)
    assert staged._scope_for(multi, MediaType.TV) == staged._scope_for(equivalent, MediaType.TV)


async def test_replacement_refuses_ambiguous_same_scope_before_qbit(approval_db, monkeypatch):
    request, _, new = await make_stage(approval_db)
    _, _, old1 = await make_stage(approval_db, request=request)
    _, _, old2 = await make_stage(approval_db, request=request)
    old1.status = old2.status = "approved"
    await approval_db.commit()
    qbit_factory = MagicMock()
    monkeypatch.setattr(staged, "QbittorrentService", qbit_factory)
    with pytest.raises(HTTPException, match="ambiguous") as blocked:
        await staged.replace_staged_torrent(new.id, reason="Better release", db=approval_db)
    assert blocked.value.status_code == 409
    qbit_factory.assert_not_called()


async def test_known_archive_and_observation_time_survive_storage_staging_and_approval(
    approval_db,
    monkeypatch,
    tmp_path,
):
    import json

    from app.siftarr.services.decisions.rule_engine_provider import get_rule_engine
    from app.siftarr.services.integrations.prowlarr_service import ProwlarrRelease
    from app.siftarr.services.releases.release_storage import (
        build_prowlarr_release,
        store_search_results,
    )
    from app.siftarr.services.releases.staging_service import StagingService

    request, _, _ = await make_stage(approval_db)
    observed = datetime.now(UTC) - timedelta(days=2)
    release = ProwlarrRelease(
        title="Example Movie 2025 1080p Archive",
        size=2 * 1024**3,
        seeders=10,
        leechers=0,
        download_url="https://example.test/archive.torrent",
        indexer="test",
        files=2,
        file_paths=("movie.part01.rar", "movie.part02.rar"),
        file_metadata_observed_at=observed,
        seeders_observed_at=observed,
    )
    rules = await get_rule_engine(approval_db, "movie")
    evaluation = rules.evaluate(release)
    records = await store_search_results(approval_db, request.id, [evaluation], purge_stale=False)
    stored = next(iter(records.values()))
    restored = build_prowlarr_release(stored)
    assert restored.file_paths == release.file_paths
    assert restored.seeders_observed_at == observed
    monkeypatch.setattr("app.siftarr.services.releases.staging_service.STAGING_DIR", tmp_path)
    stage = await StagingService(approval_db).save_release(
        release=restored,
        request=request,
        score=evaluation.total_score,
        source_release=stored,
    )
    sidecar = json.loads((tmp_path / (stage.original_filename + ".json")).read_text())
    assert sidecar["release"]["seeders_observed_at"] == observed.isoformat()
    assert release.file_paths is not None
    assert sidecar["release"]["file_paths"] == list(release.file_paths)
    warnings = await staged._approval_warnings(approval_db, stage, request)
    assert any(item["code"] == "seeders" for item in warnings)
    assert stage.score == -100
