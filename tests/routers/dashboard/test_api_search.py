"""Tests for dashboard search API routes."""

import json
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.responses import JSONResponse
from sqlalchemy import select

from app.siftarr.models.release import Release
from app.siftarr.models.request import MediaType, RequestStatus
from app.siftarr.models.stats_metrics import StatsTimingEvent
from app.siftarr.services.dashboard import search_service
from app.siftarr.services.dashboard.dashboard_service import serialize_tv_search_response
from app.siftarr.services.dashboard.search_service import SearchService
from app.siftarr.services.integrations.prowlarr_service import ProwlarrRelease, ProwlarrSearchResult
from app.siftarr.services.request_service import load_request_or_404
from tests.factories import make_request, make_season

GB = 1024**3


def _release(title: str, size_gb: float = 2, **overrides) -> ProwlarrRelease:
    fields: dict[str, Any] = {
        "title": title,
        "size": int(size_gb * GB),
        "indexer": "Indexer",
        "download_url": f"https://example.test/{title}",
        "seeders": 10,
        "leechers": 1,
    } | overrides
    return ProwlarrRelease(**fields)


def _result(*releases: ProwlarrRelease, **kwargs) -> ProwlarrSearchResult:
    return ProwlarrSearchResult(releases=list(releases), query_time_ms=5, **kwargs)


async def _search_season_packs(request_id, season_number, db):
    request = await load_request_or_404(db, request_id)
    data = await SearchService(db).search_season_packs(request, season_number=season_number)
    return JSONResponse(serialize_tv_search_response(data))


async def _search_multi_season_packs(request_id, db):
    request = await load_request_or_404(db, request_id)
    data = await SearchService(db).search_multi_season_packs(request, request_id=request_id)
    return JSONResponse(serialize_tv_search_response(data))


async def _search_episode(request_id, season_number, episode_number, db):
    request = await load_request_or_404(db, request_id)
    data = await SearchService(db).search_episode(
        request, season_number=season_number, episode_number=episode_number
    )
    return JSONResponse(serialize_tv_search_response(data))


def _body(response) -> dict:
    return json.loads(cast(bytes, response.body))


@pytest.fixture(autouse=True)
def _isolate_disposition_queries(monkeypatch):
    async def passthrough(_db, _request, evaluations):
        return evaluations

    monkeypatch.setattr(search_service, "apply_dispositions", passthrough)


@pytest.fixture
def prowlarr(monkeypatch) -> AsyncMock:
    service = AsyncMock()
    monkeypatch.setattr(search_service, "ProwlarrService", lambda settings: service)
    return service


def _stub_rules(monkeypatch, scores: dict[str, float] | None = None, default: float = 12.5):
    """Replace the rule engine with one that scores releases by title."""
    monkeypatch.setattr(
        search_service.RuleEngine,
        "from_db_rules",
        MagicMock(
            return_value=MagicMock(
                evaluate=lambda release: MagicMock(
                    total_score=(scores or {}).get(release.title, default),
                    passed=True,
                    rejection_reason=None,
                )
            )
        ),
    )


@pytest.fixture
async def show(db):
    req = await make_request(
        db, media_type=MediaType.TV, title="Foundation", year=2023, tvdb_id=999
    )
    await db.commit()
    return req


async def test_process_request_search_persists_search_completed_duration(db, monkeypatch):
    class FakeMovieDecisionService:
        def __init__(self, *args):
            pass

        async def process_request(self, request_id):
            return {"status": "pending", "message": "searched"}

    monkeypatch.setattr(search_service, "MovieDecisionService", FakeMovieDecisionService)
    request = await make_request(db, title="Duration Movie")
    await db.commit()

    await SearchService(db).process_request_search(request)
    await db.commit()

    timing = (
        await db.execute(
            select(StatsTimingEvent).where(StatsTimingEvent.event_name == "search_completed")
        )
    ).scalar_one()
    assert timing.duration_ms is not None
    assert timing.duration_ms >= 0


async def test_search_multi_season_packs_returns_coverage_metadata(db, show, prowlarr, monkeypatch):
    """Multi-season endpoint surfaces season coverage for broad TV packs."""
    for number in (1, 2, 3):
        await make_season(db, show, number)
    await db.commit()
    prowlarr.search_tv_season_sweep.return_value = _result(
        _release("Foundation.S01-S03.2160p.WEB-DL", 30),
        _release("Foundation.S01-03.1080p.WEB-DL", 28),
        _release("Foundation.Complete.1080p.BluRay", 42),
        _release("Foundation.Complete.S01.1080p.BluRay", 14),
        _release("Foundation.S02E01.1080p.WEB-DL"),
    )
    _stub_rules(monkeypatch)

    body = _body(await _search_multi_season_packs(request_id=show.id, db=db))

    assert body["scope"] == {"type": "multi_season_packs"}
    assert body["known_total_seasons"] == 3
    compact, ranged, complete = body["releases"]
    assert [r["title"] for r in body["releases"]] == [
        "Foundation.S01-03.1080p.WEB-DL",
        "Foundation.S01-S03.2160p.WEB-DL",
        "Foundation.Complete.1080p.BluRay",
    ]
    assert compact["covered_seasons"] == [1, 2, 3]
    assert compact["covers_all_known_seasons"] is True
    assert compact["is_complete_series"] is False
    assert compact["size_per_season"] == "9.33 GB"
    assert compact["size_per_season_bytes"] == round(28 * GB / 3)
    assert ranged["covered_seasons"] == [1, 2, 3]
    assert ranged["size_per_season"] == "10.00 GB"
    assert complete["covered_seasons"] == []
    assert complete["is_complete_series"] is True
    assert complete["size_per_season"] == "14.00 GB"
    assert compact["status"] == "passed"
    assert compact["stored_release_id"] is not None


@pytest.mark.parametrize(
    ("kind", "found", "expected"),
    [
        (
            "season",
            [
                _release("Foundation.S01.2160p.WEB-DL", 30),
                _release("Foundation.S01-S03.2160p.WEB-DL", 42),
                _release("Foundation.Complete.Series.1080p.BluRay", 55),
                _release("Foundation.Complete.S01.1080p.BluRay", 28),
                _release("Foundation.S01E01.1080p.WEB-DL"),
            ],
            ["Foundation.Complete.S01.1080p.BluRay", "Foundation.S01.2160p.WEB-DL"],
        ),
        (
            "episode",
            [
                _release("Foundation.S01E01.1080p.WEB-DL"),
                _release("Foundation.S01E02.1080p.WEB-DL"),
                _release("Foundation.S01E01E02.1080p.WEB-DL", 3),
                _release("Foundation.S01E01-E02.1080p.WEB-DL", 3),
            ],
            ["Foundation.S01E01.1080p.WEB-DL"],
        ),
    ],
)
async def test_season_and_episode_search_keep_only_exact_scope_releases(
    db, show, prowlarr, monkeypatch, kind, found, expected
):
    _stub_rules(monkeypatch)
    if kind == "season":
        prowlarr.search_tv_season_sweep.return_value = _result(*found)
        body = _body(await _search_season_packs(request_id=show.id, season_number=1, db=db))
        assert body["scope"] == {"type": "season_packs", "season_number": 1}
    else:
        prowlarr.search_tv_episode_exact.return_value = _result(*found)
        body = _body(await _search_episode(show.id, 1, 1, db))
        assert body["scope"] == {
            "type": "single_episode",
            "season_number": 1,
            "episode_number": 1,
        }

    assert [release["title"] for release in body["releases"]] == expected


async def test_search_season_packs_skips_partly_available_season(db, show, prowlarr):
    await make_season(db, show, 1, episodes=1, status=RequestStatus.COMPLETED)
    await db.commit()

    body = _body(await _search_season_packs(request_id=show.id, season_number=1, db=db))

    assert body["releases"] == []
    assert "skipped" in body["error"]
    prowlarr.search_tv_season_sweep.assert_not_awaited()


@pytest.mark.parametrize(
    ("kind", "titles"),
    [
        (
            "season",
            (
                "Foundation.S01.2160p.WEB-DL",
                "Foundation.Complete.S01.1080p.BluRay",
                "Foundation.S01.REMUX",
            ),
        ),
        (
            "multi_season",
            (
                "Foundation.S01-S02.2160p.WEB-DL",
                "Foundation.S01-02.1080p.WEB-DL",
                "Foundation.Complete.720p.WEB-DL",
            ),
        ),
        (
            "episode",
            (
                "Foundation.S01E01.2160p.WEB-DL",
                "Foundation.S01E01.1080p.WEB-DL",
                "Foundation.S01E01.HDTV",
            ),
        ),
    ],
)
async def test_search_orders_by_score_then_smaller_size(
    db, show, prowlarr, monkeypatch, kind, titles
):
    larger, smaller, lower = titles
    found = _result(_release(larger, 30), _release(lower, 10), _release(smaller, 14))
    _stub_rules(monkeypatch, {larger: 100, smaller: 100, lower: 90})
    if kind == "episode":
        prowlarr.search_tv_episode_exact.return_value = found
        response = await _search_episode(show.id, 1, 1, db)
    elif kind == "season":
        prowlarr.search_tv_season_sweep.return_value = found
        response = await _search_season_packs(request_id=show.id, season_number=1, db=db)
    else:
        await make_season(db, show, 1)
        await make_season(db, show, 2)
        await db.commit()
        prowlarr.search_tv_season_sweep.return_value = found
        response = await _search_multi_season_packs(request_id=show.id, db=db)

    releases = _body(response)["releases"]

    assert [release["title"] for release in releases] == [smaller, larger, lower]
    assert all("_size_bytes" not in release for release in releases)


async def test_search_season_packs_prioritizes_size_limit_passes(db, show, prowlarr, monkeypatch):
    """Non-size failures rank above size failures, and only size failures are flagged."""
    other_rule_fail = _release("Foundation.S01.1080p.WEB-DL.BADTAG", 14)
    size_fail = _release("Foundation.S01.2160p.REMUX", 40)
    prowlarr.search_tv_season_sweep.return_value = _result(size_fail, other_rule_fail)
    scores = {other_rule_fail.title: 80, size_fail.title: 100}
    reasons = {
        size_fail.title: "Size 40.00 GB above maximum 20.00 GB",
        other_rule_fail.title: "Matched exclusion pattern: Bad Tag",
    }
    monkeypatch.setattr(
        search_service.RuleEngine,
        "from_db_rules",
        MagicMock(
            return_value=MagicMock(
                evaluate=lambda release: MagicMock(
                    total_score=scores[release.title],
                    passed=False,
                    rejection_reason=reasons[release.title],
                )
            )
        ),
    )

    body = _body(await _search_season_packs(request_id=show.id, season_number=1, db=db))

    first, second = body["releases"]
    assert [first["title"], second["title"]] == [other_rule_fail.title, size_fail.title]
    assert first["passed"] is False
    assert first["size_per_season_passed"] is None
    assert second["size_per_season_passed"] is False


async def test_search_episode_refreshes_exact_results_after_cached_sweep(db, show, prowlarr):
    """A manual episode search must refresh exact results even with a cached match."""
    exact = _release("Foundation.S02E01.1080p.WEB-DL")
    rejected = _release("Foundation.S02E02.BADTAG.1080p.WEB-DL", seeders=1)
    prowlarr.search_tv_season_sweep.return_value = _result(exact, rejected)
    service = SearchService(db)
    await service.search_season_packs(show, season_number=2)
    prowlarr.reset_mock()
    refreshed = _release("Foundation.S02E01.REPACK.1080p.WEB-DL", seeders=30)
    prowlarr.search_tv_episode_exact.return_value = _result(refreshed)

    result = await service.search_episode(show, season_number=2, episode_number=1)

    assert [release["title"] for release in result.releases] == [refreshed.title]
    assert result.releases[0]["stored_release_id"] is not None
    prowlarr.search_tv_season_sweep.assert_not_awaited()
    prowlarr.search_tv_episode_exact.assert_awaited_once()
    stored = (
        (await db.execute(select(Release.title).where(Release.request_id == show.id)))
        .scalars()
        .all()
    )
    assert exact.title not in stored
    assert rejected.title in stored
    assert refreshed.title in stored


async def test_search_episode_falls_back_to_exact_query_when_sweep_misses_it(db, prowlarr):
    """Old episodes buried beyond the season sweep caps stay discoverable, and results for
    another show that merely mention the title are dropped."""
    show = await make_request(
        db, media_type=MediaType.TV, title="Georgie & Mandy's First Marriage", year=2024
    )
    exact = _release("Georgie.And.Mandys.First.Marriage.S02E01.1080p.WEB-DL")
    wrong_show = _release("Evil.S02E01.Georgie.And.Mandys.First.Marriage.1080p.WEB-DL", seeders=99)
    prowlarr.search_tv_episode_exact.return_value = _result(wrong_show, exact)
    service = SearchService(db)
    await service.search_season_packs(show, season_number=2)
    prowlarr.reset_mock()

    result = await service.search_episode(show, season_number=2, episode_number=1)

    assert [release["title"] for release in result.releases] == [exact.title]
    prowlarr.search_tv_season_sweep.assert_not_awaited()
    prowlarr.search_tv_episode_exact.assert_awaited_once_with(
        title="Georgie & Mandy's First Marriage",
        season=2,
        episode=1,
        cacheable=False,
        request_id=show.id,
    )
    stored = (await db.execute(select(Release.title))).scalars().all()
    assert wrong_show.title not in stored


async def test_search_episode_returns_exact_provider_error(db, show, prowlarr):
    prowlarr.search_tv_episode_exact.return_value = _result(error="HTTP 503")

    body = _body(await _search_episode(show.id, 1, 1, db))

    assert body["releases"] == []
    assert body["error"] == "HTTP 503"


async def test_search_multi_season_packs_includes_broad_pack_query(db, show, prowlarr, monkeypatch):
    """Per-season sweeps cannot match "S01-S03"/"Complete" titles, so the broad
    title-only pack query must run and its results must reach the response."""
    await make_season(db, show, 1)
    await make_season(db, show, 2)
    await db.commit()
    prowlarr.search_tv_season_sweep.return_value = _result()
    prowlarr.search_tv_packs_broad.return_value = _result(
        _release("Foundation.S01-S02.1080p.WEB-DL", 20)
    )
    _stub_rules(monkeypatch)

    body = _body(await _search_multi_season_packs(request_id=show.id, db=db))

    prowlarr.search_tv_packs_broad.assert_awaited_once()
    assert prowlarr.search_tv_packs_broad.await_args.kwargs["title"] == "Foundation"
    assert [r["title"] for r in body["releases"]] == ["Foundation.S01-S02.1080p.WEB-DL"]


async def test_search_multi_season_packs_survives_broad_query_failure(
    db, show, prowlarr, monkeypatch
):
    """A failing broad query must not lose the per-season sweep results."""
    await make_season(db, show, 1)
    await db.commit()
    prowlarr.search_tv_season_sweep.return_value = _result(
        _release("Foundation.Complete.1080p.BluRay", 40)
    )
    prowlarr.search_tv_packs_broad.side_effect = RuntimeError("indexer down")
    _stub_rules(monkeypatch)

    body = _body(await _search_multi_season_packs(request_id=show.id, db=db))

    assert [r["title"] for r in body["releases"]] == ["Foundation.Complete.1080p.BluRay"]
