"""Integration-style validation for TV season sweeps."""

from unittest.mock import MagicMock

import pytest
from fastapi import BackgroundTasks
from sqlalchemy import select

from app.siftarr.config import Settings
from app.siftarr.models.episode import Episode
from app.siftarr.models.release import Release
from app.siftarr.models.request import MediaType, RequestStatus
from app.siftarr.services.dashboard.detail_service import DetailService
from app.siftarr.services.decisions import tv_decision_service
from app.siftarr.services.decisions.rule_engine import ReleaseEvaluation, RuleEngine
from app.siftarr.services.decisions.tv_decision_service import TVDecisionService
from app.siftarr.services.integrations.prowlarr_service import (
    ProwlarrRelease,
    ProwlarrSearchResult,
    ProwlarrService,
)
from app.siftarr.services.integrations.qbittorrent_service import QbittorrentService
from app.siftarr.services.releases import staging_service
from app.siftarr.services.releases.release_storage import get_release_persistence_key
from tests.factories import make_episode, make_request, make_season

Key = tuple[int, int]


def _release(title: str, info_hash: str) -> ProwlarrRelease:
    return ProwlarrRelease(
        title=title,
        size=1024**3,
        seeders=50,
        leechers=0,
        download_url=f"https://ipt.example/{info_hash}",
        info_hash=info_hash,
        indexer="IPTorrents",
    )


def _result(releases: list[ProwlarrRelease], *, hit_limit: bool = False) -> ProwlarrSearchResult:
    return ProwlarrSearchResult(releases=releases, query_time_ms=10, hit_limit=hit_limit)


class ScriptedProwlarr(ProwlarrService):
    """Prowlarr stub that returns scripted results and records which searches ran."""

    def __init__(
        self,
        *,
        sweeps: dict[int, ProwlarrSearchResult] | None = None,
        exact: dict[Key, list[ProwlarrRelease]] | None = None,
        packs: list[ProwlarrRelease] | None = None,
        settings: Settings | None = None,
    ) -> None:
        super().__init__(settings or Settings())
        self.sweeps = sweeps or {}
        self.exact = exact or {}
        self.packs = packs or []
        self.swept_seasons: list[int] = []
        self.exact_episode_calls: list[Key] = []
        self.pack_calls = 0

    async def search_tv_season_sweep(self, title, season, *args, **kwargs):
        self.swept_seasons.append(season)
        return self.sweeps.get(season, _result([]))

    async def search_tv_episode_exact(self, title, season, episode, *args, **kwargs):
        self.exact_episode_calls.append((season, episode))
        return _result(self.exact.get((season, episode), []))

    async def search_tv_packs_broad(self, *args, **kwargs):
        self.pack_calls += 1
        return _result(self.packs)


class RejectNamedRuleEngine(RuleEngine):
    def evaluate(
        self, release: ProwlarrRelease, *, allow_episode_size_fallback: bool = False
    ) -> ReleaseEvaluation:
        if "REJECT" in release.title:
            return ReleaseEvaluation(
                release=release,
                passed=False,
                total_score=-100,
                matches=[],
                rejection_reason="Matched exclusion pattern: reject marker",
            )
        score = 200 if "S01-S02" in release.title else 100 if "S01." in release.title else 10
        return ReleaseEvaluation(
            release=release, passed=True, total_score=score, matches=[], rejection_reason=None
        )


@pytest.fixture
def make_service(db, monkeypatch, tmp_path):
    # Run the real StagingService in staging mode; only the staging directory
    # and runtime settings are replaced.
    monkeypatch.setattr(staging_service, "STAGING_DIR", tmp_path)
    monkeypatch.setattr(
        staging_service, "get_settings", lambda: MagicMock(staging_mode_enabled=True)
    )

    def build(prowlarr: ScriptedProwlarr) -> TVDecisionService:
        service = TVDecisionService(db, prowlarr, QbittorrentService())

        async def rule_engine() -> RejectNamedRuleEngine:
            return RejectNamedRuleEngine()

        monkeypatch.setattr(service, "_get_rule_engine", rule_engine)
        return service

    return build


async def _stored_titles(db) -> set[str]:
    return {r.title for r in (await db.execute(select(Release))).scalars()}


async def _episode_statuses(db) -> dict[Key, RequestStatus]:
    episodes = (await db.execute(select(Episode))).scalars().all()
    return {(e.season_id, e.episode_number): e.status for e in episodes}


async def _details(db, request, limit: int = 25):
    return await DetailService(db).load_request_details(
        request, request_id=request.id, background_tasks=BackgroundTasks(), limit=limit
    )


async def test_full_search_persists_buckets_and_statuses(db, make_service) -> None:
    request = await make_request(db, media_type=MediaType.TV, title="Show", year=2024)
    await make_season(db, request, 1, episodes=3)
    await make_season(db, request, 2, episodes=1)
    prowlarr = ScriptedProwlarr(
        settings=Settings(
            prowlarr_tv_page_size=100,
            prowlarr_tv_strategy_title_sxx_enabled=True,
            prowlarr_tv_strategy_imdb_enabled=False,
            prowlarr_tv_strategy_title_season_token_enabled=False,
            prowlarr_tv_strategy_tvdb_enabled=False,
        ),
        exact={
            (1, 1): [_release("Show.S01E01.1080p", "exact-s01e01")],
            (1, 2): [_release("Show.S01E02.1080p", "exact-s01e02")],
            (1, 3): [_release("Show.S01E03.REJECT.1080p", "rejected-s01e03")],
            (2, 1): [_release("Show.S02E01.1080p", "exact-s02e01")],
        },
        packs=[
            _release("Show.S01E01.1080p", "exact-s01e01"),
            _release("Show.S01.1080p", "season-s01"),
            _release("Show.S01-S02.1080p", "multi-s01-s02"),
        ],
    )

    result = await make_service(prowlarr).process_request(request.id, search_mode="full")

    assert result["status"] == "staged"
    assert prowlarr.exact_episode_calls == [(1, 1), (1, 2), (1, 3), (2, 1)]
    assert prowlarr.pack_calls == 1

    stored = (await db.execute(select(Release))).scalars().all()
    assert len(stored) == 6  # Exact episodes plus broad pack results, with one duplicate.
    by_title = {release.title: release for release in stored}
    assert by_title["Show.S01E01.1080p"].episode_number == 1
    assert by_title["Show.S01.1080p"].season_number == 1
    assert by_title["Show.S01-S02.1080p"].season_coverage == "1,2"
    rejected = by_title["Show.S01E03.REJECT.1080p"]
    assert rejected.passed_rules is False
    assert rejected.rejection_reason is not None

    assert set((await _episode_statuses(db)).values()) == {RequestStatus.STAGED}

    details = await _details(db, request)
    assert details.total_releases == len(details.releases) == 6
    assert details.tv_info is not None
    assert {"1", "2"} <= set(details.tv_info.releases_by_season)
    assert "1-1" in details.tv_info.releases_by_episode
    assert any(r["title"] == "Show.S01-S02.1080p" for r in details.tv_info.releases_by_season["2"])


@pytest.mark.parametrize(
    ("episode_one_status", "selected", "swept", "exact_calls", "expected_statuses"),
    [
        pytest.param(
            RequestStatus.COMPLETED,
            ["Show.S01E02.1080p"],
            [],
            [(1, 2)],
            [RequestStatus.COMPLETED, RequestStatus.STAGED],
            id="partly-available-skips-pack",
        ),
        pytest.param(
            RequestStatus.PENDING,
            ["Show.S01.1080p"],
            [1],
            [],
            [RequestStatus.STAGED, RequestStatus.STAGED],
            id="fully-missing-takes-pack",
        ),
    ],
)
async def test_season_pack_only_used_when_whole_season_missing(
    db, make_service, episode_one_status, selected, swept, exact_calls, expected_statuses
) -> None:
    request = await make_request(db, media_type=MediaType.TV, title="Show", year=2024)
    season = await make_season(db, request, 1)

    await make_episode(db, season, 1, status=episode_one_status)
    await make_episode(db, season, 2)
    prowlarr = ScriptedProwlarr(
        sweeps={1: _result([_release("Show.S01.1080p", "season-s01")])},
        exact={(1, 2): [_release("Show.S01E02.1080p", "exact-s01e02")]},
    )

    result = await make_service(prowlarr).process_request(request.id)

    assert result["status"] == "staged"
    assert [r["title"] for r in result["selected_releases"]] == selected
    assert prowlarr.swept_seasons == swept
    assert prowlarr.exact_episode_calls == exact_calls
    statuses = await _episode_statuses(db)
    assert [statuses[(season.id, n)] for n in (1, 2)] == expected_statuses
    if exact_calls:
        assert await _stored_titles(db) == set(selected)


async def test_capped_sweep_still_checks_episodes_missing_from_results(db, make_service) -> None:
    request = await make_request(db, media_type=MediaType.TV, title="Show", year=2024)
    await make_season(db, request, 2, episodes=2)
    await make_season(db, request, 3)
    prowlarr = ScriptedProwlarr(
        sweeps={2: _result([_release("Show.S02E02.1080p", "s02e02-sweep")], hit_limit=True)},
        exact={(2, 2): [_release("Show.S02E02.1080p", "s02e02-exact")]},
    )

    result = await make_service(prowlarr).process_request(request.id)

    assert result["status"] == "staged"
    assert prowlarr.swept_seasons == [2]
    assert prowlarr.exact_episode_calls == [(2, 1), (2, 2)]
    assert await _stored_titles(db) == {"Show.S02E02.1080p"}
    details = await _details(db, request)
    assert details.tv_info is not None
    assert set(details.tv_info.releases_by_episode) == {"2-2"}


async def test_capped_sweep_episodes_persist_and_stay_visible_when_paginated(
    db, make_service
) -> None:
    show = "Georgie.and.Mandys.First.Marriage"

    def episode_title(season: int, episode: int) -> str:
        return f"{show}.S{season:02d}E{episode:02d}.1080p.WEB-DL"

    swept_episodes = range(12, 19)
    request = await make_request(db, media_type=MediaType.TV, title=show, year=2024)
    await make_season(db, request, 1, episodes=1)
    await make_season(db, request, 2, episodes=22)
    prowlarr = ScriptedProwlarr(
        sweeps={
            1: _result([_release(episode_title(1, 1), "s01e01-sweep")], hit_limit=True),
            2: _result(
                [_release(episode_title(2, e), f"s02e{e}-sweep") for e in swept_episodes],
                hit_limit=True,
            ),
        },
        exact={
            (1, 1): [_release(episode_title(1, 1), "s01e01-exact")],
            **{(2, e): [_release(episode_title(2, e), f"s02e{e}-exact")] for e in swept_episodes},
        },
    )

    result = await make_service(prowlarr).process_request(request.id)

    assert result["status"] == "staged"
    assert prowlarr.swept_seasons == [1, 2]
    assert prowlarr.exact_episode_calls == [(1, 1), *((2, e) for e in range(1, 23))]

    stored_titles = await _stored_titles(db)
    assert episode_title(2, 1) not in stored_titles
    assert {episode_title(2, e) for e in swept_episodes} <= stored_titles

    details = await _details(db, request, limit=2)
    assert details.tv_info is not None
    assert details.filtered_total_releases > len(details.releases)
    visible = {r["title"] for r in details.releases}
    bucketed = {r["title"] for b in details.tv_info.releases_by_episode.values() for r in b}
    # TV buckets use the complete filtered result set, not the generic
    # paginated release page, so season/pack rows remain visible.
    assert visible <= bucketed


async def test_selection_missing_after_persistence_is_not_marked_staged(
    db, make_service, monkeypatch
) -> None:
    request = await make_request(db, media_type=MediaType.TV, title="Show", year=2024)
    season = await make_season(db, request, 1, episodes=2)
    lost = _release("Show.S01E02.1080p", "exact-s01e02")
    real_store = tv_decision_service.store_search_results

    async def store_dropping_lost(*args, **kwargs):
        stored = await real_store(*args, **kwargs)
        stored.pop(get_release_persistence_key(title=lost.title, info_hash=lost.info_hash), None)
        return stored

    monkeypatch.setattr(tv_decision_service, "store_search_results", store_dropping_lost)
    prowlarr = ScriptedProwlarr(
        exact={(1, 1): [_release("Show.S01E01.1080p", "exact-s01e01")], (1, 2): [lost]},
    )

    result = await make_service(prowlarr).process_request(request.id)

    assert result["status"] == "staged"
    statuses = await _episode_statuses(db)
    assert [statuses[(season.id, n)] for n in (1, 2)] == [
        RequestStatus.STAGED,
        RequestStatus.PENDING,
    ]
