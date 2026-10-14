import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from app.siftarr.models.release import Release
from app.siftarr.models.request import Request, RequestStatus
from app.siftarr.models.rule import RuleType
from app.siftarr.models.staged_torrent import StagedTorrent
from app.siftarr.services import staging_decision_log
from app.siftarr.services.decisions.movie_decision_service import MovieDecisionService
from app.siftarr.services.integrations.prowlarr_service import ProwlarrRelease, ProwlarrSearchResult
from tests.factories import make_request, make_rule

GB = 1024**3


def _release(
    title: str, url: str = "good", *, size: int = 1024, seeders: int = 10, indexer: str = "A"
) -> ProwlarrRelease:
    return ProwlarrRelease(
        title=title,
        size=size,
        seeders=seeders,
        leechers=0,
        download_url=f"https://example.test/{url}.torrent",
        indexer=indexer,
    )


def _service(db, *releases: ProwlarrRelease) -> MovieDecisionService:
    prowlarr = AsyncMock()
    prowlarr.search_by_tmdbid.return_value = ProwlarrSearchResult(
        releases=list(releases), query_time_ms=10
    )
    return MovieDecisionService(db, prowlarr, AsyncMock())


@pytest.fixture
def decision_log(monkeypatch, tmp_path):
    path = tmp_path / "decision-log.jsonl"
    monkeypatch.setattr(staging_decision_log, "STAGING_DECISION_LOG_PATH", path)
    return path


@pytest.fixture
def staged_calls(monkeypatch) -> list[tuple[Request, list, str]]:
    """Replace StagingService with a fake that records the selected releases."""
    calls: list[tuple[Request, list, str]] = []

    class FakeStaging:
        def __init__(self, db):
            self.db = db

        async def use_releases(self, request, releases, *, selection_source="manual"):
            calls.append((request, releases, selection_source))
            return {"status": "staged", "message": "staged"}

    monkeypatch.setattr(
        "app.siftarr.services.decisions.movie_decision_service.StagingService", FakeStaging
    )
    return calls


@pytest.mark.asyncio
async def test_movie_identity_mismatch_is_persisted_and_not_selected(
    db, decision_log, staged_calls
):
    request = await make_request(db, title="The Cheetah Girls", year=2003, tmdb_id=32293)
    service = _service(db, _release("The.Cheetah.Girls.2.2005.1080p.WEB-DL", "bad"))

    result = await service.process_request(request.id)

    assert result["status"] == "pending"
    assert staged_calls == []
    stored = (await db.execute(select(Release))).scalar_one()
    assert stored.passed_rules is False
    assert stored.rejection_reason is not None
    assert "release title 'The.Cheetah.Girls.2'" in stored.rejection_reason
    refreshed = await db.get(Request, request.id)
    assert refreshed is not None
    assert refreshed.status == RequestStatus.PENDING
    assert "Movie identity mismatch" in (refreshed.rejection_reason or "")
    entry = json.loads(decision_log.read_text().splitlines()[0])
    assert entry["event_type"] == "all_rejected"
    assert entry["all_candidates"][0]["download_url"] == "https://example.test/bad.torrent"
    assert entry["all_candidates"][0]["rule_matches"] == entry["all_candidates"][0]["matches"]


@pytest.mark.asyncio
async def test_movie_identity_filter_allows_exact_title_missing_release_year(
    db, decision_log, staged_calls
):
    request = await make_request(db, title="The Cheetah Girls", year=2003, tmdb_id=32293)
    service = _service(db, _release("The.Cheetah.Girls.1080p.WEB-DL"))

    result = await service.process_request(request.id)

    assert result["status"] == "staged"
    assert [(c[1][0].title, c[2]) for c in staged_calls] == [
        ("The.Cheetah.Girls.1080p.WEB-DL", "rule")
    ]
    entry = json.loads(decision_log.read_text().splitlines()[0])
    assert entry["selected_release"]["title"] == "The.Cheetah.Girls.1080p.WEB-DL"
    assert entry["top_candidates"]
    stored = (await db.execute(select(Release))).scalar_one()
    assert stored.passed_rules is True
    assert stored.rejection_reason is None


@pytest.mark.asyncio
async def test_movie_db_rules_score_and_persist_selected_staged_torrent(db, monkeypatch, tmp_path):
    monkeypatch.setattr("app.siftarr.services.releases.staging_service.STAGING_DIR", tmp_path)
    monkeypatch.setattr(
        "app.siftarr.services.releases.staging_service.get_settings",
        lambda: SimpleNamespace(staging_mode_enabled=True),
    )
    request = await make_request(db, year=2024)
    await make_rule(
        db,
        name="Movie size",
        rule_type=RuleType.SIZE_LIMIT,
        media_scope="movie",
        pattern="size_limit",
        min_size_gb=1,
        max_size_gb=10,
    )
    await make_rule(
        db,
        name="Require 1080p",
        rule_type=RuleType.REQUIREMENT,
        media_scope="movie",
        pattern="1080p",
    )
    await make_rule(
        db,
        name="Movie x265",
        rule_type=RuleType.SCORER,
        media_scope="movie",
        pattern="x265",
        score=50,
    )
    await make_rule(
        db,
        name="Both WEB",
        rule_type=RuleType.SCORER,
        media_scope="both",
        pattern="WEB-DL",
        score=5,
    )
    await db.commit()
    x264 = "Example.Movie.2024.1080p.WEB-DL.x264-GRP"
    x265 = "Example.Movie.2024.1080p.WEB-DL.x265-GRP"
    service = _service(
        db,
        _release(x264, "x264", size=2 * GB, seeders=20, indexer="B"),
        _release(x265, "x265", size=2 * GB),
    )

    result = await service.process_request(request.id)

    assert result["status"] == "staged"
    assert result["selected_release"]["title"] == x265
    assert result["selected_release"]["score"] == 55
    releases = (await db.execute(select(Release))).scalars().all()
    assert {r.title: r.score for r in releases} == {x264: 5, x265: 55}
    staged = (await db.execute(select(StagedTorrent))).scalar_one()
    assert (staged.title, staged.score, staged.selection_source) == (x265, 55, "rule")


@pytest.mark.asyncio
async def test_movie_best_release_tie_prefers_seeders_not_input_order(db, staged_calls):
    request = await make_request(db, title="Tie Movie", year=2024, tmdb_id=2)
    service = _service(
        db,
        _release("Tie.Movie.2024.1080p.WEB-DL.LowSeed", "low", seeders=1, indexer="B"),
        _release("Tie.Movie.2024.1080p.WEB-DL.HighSeed", "high", seeders=50),
    )

    result = await service.process_request(request.id)

    assert result["status"] == "staged"
    assert [c[1][0].title for c in staged_calls] == ["Tie.Movie.2024.1080p.WEB-DL.HighSeed"]
    assert result["selected_release"]["score"] == 0
