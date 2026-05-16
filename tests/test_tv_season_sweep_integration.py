"""Integration-style validation for TV season sweeps."""

from datetime import date

import pytest
from fastapi import BackgroundTasks
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.siftarr.config import Settings
from app.siftarr.models import Base
from app.siftarr.models.episode import Episode
from app.siftarr.models.release import Release
from app.siftarr.models.request import MediaType, Request, RequestStatus
from app.siftarr.models.season import Season
from app.siftarr.services.dashboard.detail_service import DetailService
from app.siftarr.services.decisions.rule_engine import ReleaseEvaluation, RuleEngine
from app.siftarr.services.decisions.tv_decision_service import TVDecisionService
from app.siftarr.services.integrations.prowlarr_service import (
    ProwlarrRelease,
    ProwlarrSearchResult,
    ProwlarrService,
)
from app.siftarr.services.integrations.qbittorrent_service import QbittorrentService


def _release(title: str, index: int, *, info_hash: str | None = None) -> ProwlarrRelease:
    return ProwlarrRelease(
        title=title,
        size=1024 * 1024 * 1024,
        seeders=100 - (index % 50),
        leechers=0,
        download_url=f"https://ipt.example/{info_hash or index}",
        info_hash=info_hash,
        indexer="IPTorrents",
    )


class FakeIPTProwlarr(ProwlarrService):
    def __init__(self) -> None:
        super().__init__(
            Settings(
                prowlarr_tv_page_size=100,
                prowlarr_tv_strategy_title_sxx_enabled=True,
                prowlarr_tv_strategy_imdb_enabled=False,
                prowlarr_tv_strategy_title_season_token_enabled=False,
                prowlarr_tv_strategy_tvdb_enabled=False,
            )
        )
        self.calls: list[dict] = []

    async def _search(self, params: dict, cacheable: bool = True) -> ProwlarrSearchResult:
        self.calls.append(params)
        offset = params["offset"]
        if offset == 0:
            releases = [
                _release("Show.S01E01.1080p", 1, info_hash="exact-s01e01"),
                _release("Show.S02E01.1080p", 2, info_hash="exact-s02e01"),
                _release("Show.S01.1080p", 3, info_hash="season-s01"),
                _release("Show.S01-S02.1080p", 4, info_hash="multi-s01-s02"),
                _release("Show.S01E03.REJECT.1080p", 5, info_hash="rejected-s01e03"),
            ]
            releases.extend(
                _release(f"Show.Unrelated.Result.{i:03d}.1080p", i, info_hash=f"filler-{i}")
                for i in range(5, 100)
            )
        elif offset == 100:
            releases = [
                _release("Show.S01E01.1080p", 101, info_hash="exact-s01e01"),
                _release("Show.S01E02.1080p", 102, info_hash="exact-s01e02"),
            ]
            releases.extend(
                _release(f"Show.Final.Page.{i:03d}.1080p", i, info_hash=f"final-filler-{i}")
                for i in range(103, 111)
            )
        else:
            releases = []
        return ProwlarrSearchResult(releases=releases, query_time_ms=10)


class RejectNamedRuleEngine(RuleEngine):
    def evaluate(self, release: ProwlarrRelease) -> ReleaseEvaluation:
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
            release=release,
            passed=True,
            total_score=score,
            matches=[],
            rejection_reason=None,
        )


class FakeCappedEpisodeGapProwlarr(ProwlarrService):
    def __init__(self) -> None:
        super().__init__(Settings())
        self.swept_seasons: list[int] = []
        self.exact_episode_calls: list[tuple[int, int]] = []

    async def search_tv_season_sweep(
        self,
        title: str,
        season: int,
        imdbid: str | int | None = None,
        tvdbid: int | None = None,
        categories: list[int] | None = None,
        cacheable: bool = True,
        request_id: int | None = None,
        progress_callback=None,
    ) -> ProwlarrSearchResult:
        self.swept_seasons.append(season)
        return ProwlarrSearchResult(
            releases=[_release("Show.S02E02.1080p", 2, info_hash="s02e02-sweep")],
            query_time_ms=10,
            hit_limit=True,
        )

    async def search_by_tvdbid(
        self,
        tvdbid: int,
        title: str | None = None,
        season: int | None = None,
        episode: int | None = None,
        year: int | None = None,
        categories: list[int] | None = None,
        cacheable: bool = True,
    ) -> ProwlarrSearchResult:
        assert season is not None and episode is not None
        self.exact_episode_calls.append((season, episode))
        return ProwlarrSearchResult(
            releases=[_release("Show.S02E01.1080p", 1, info_hash="s02e01-exact")],
            query_time_ms=10,
        )


class FakeCappedGeorgieProwlarr(ProwlarrService):
    def __init__(self) -> None:
        super().__init__(Settings())
        self.swept_seasons: list[int] = []
        self.exact_episode_calls: list[tuple[int, int]] = []

    async def search_tv_season_sweep(
        self,
        title: str,
        season: int,
        imdbid: str | int | None = None,
        tvdbid: int | None = None,
        categories: list[int] | None = None,
        cacheable: bool = True,
        request_id: int | None = None,
        progress_callback=None,
    ) -> ProwlarrSearchResult:
        self.swept_seasons.append(season)
        if season == 1:
            releases = [
                _release(
                    "Georgie.And.Mandys.First.Marriage.S01E01.1080p.WEB-DL",
                    101,
                    info_hash="georgie-s01e01",
                )
            ]
        else:
            releases = [
                _release(
                    f"Georgie.and.Mandys.First.Marriage.S02E{episode:02d}.1080p.WEB-DL",
                    episode,
                    info_hash=f"georgie-s02e{episode:02d}-sweep",
                )
                for episode in range(12, 19)
            ]
        return ProwlarrSearchResult(releases=releases, query_time_ms=10, hit_limit=True)

    async def search_by_tvdbid(
        self,
        tvdbid: int,
        title: str | None = None,
        season: int | None = None,
        episode: int | None = None,
        year: int | None = None,
        categories: list[int] | None = None,
        cacheable: bool = True,
    ) -> ProwlarrSearchResult:
        assert season is not None and episode is not None
        self.exact_episode_calls.append((season, episode))
        if (season, episode) == (2, 1):
            return ProwlarrSearchResult(
                releases=[
                    _release(
                        "Georgie.And.Mandys.First.Marriage.S02E01.1080p.WEB-DL",
                        1,
                        info_hash="georgie-s02e01-fallback",
                    )
                ],
                query_time_ms=10,
            )
        return ProwlarrSearchResult(releases=[], query_time_ms=10)


@pytest.mark.asyncio
async def test_tv_request_season_sweep_persists_buckets_and_statuses(monkeypatch) -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    try:
        async with session_maker() as db:
            request = Request(
                external_id="tv-1",
                media_type=MediaType.TV,
                tmdb_id=999,
                tvdb_id=12345,
                title="Show",
                year=2024,
                status=RequestStatus.PENDING,
            )
            s1 = Season(season_number=1, status=RequestStatus.PENDING)
            s2 = Season(season_number=2, status=RequestStatus.PENDING)
            s1.episodes = [
                Episode(episode_number=i, title=f"S1E{i}", air_date=date(2024, 1, i))
                for i in range(1, 4)
            ]
            s2.episodes = [Episode(episode_number=1, title="S2E1", air_date=date(2024, 2, 1))]
            request.seasons = [s1, s2]
            db.add(request)
            await db.commit()

            class FakeStagingService:
                def __init__(self, _db):
                    pass

                async def use_releases(self, _request, _releases, *, selection_source: str):
                    return {"status": "staged", "message": "staged"}

            monkeypatch.setattr(
                "app.siftarr.services.decisions.tv_decision_service.StagingService",
                FakeStagingService,
            )

            prowlarr = FakeIPTProwlarr()
            service = TVDecisionService(db, prowlarr, QbittorrentService())

            async def fake_rule_engine() -> RejectNamedRuleEngine:
                return RejectNamedRuleEngine()

            monkeypatch.setattr(service, "_get_rule_engine", fake_rule_engine)

            result = await service.process_request(request.id)

            assert result["status"] == "staged"
            assert [call["offset"] for call in prowlarr.calls] == [0, 100, 0, 100]
            assert all("limit" not in call for call in prowlarr.calls)

            stored = (await db.execute(select(Release))).scalars().all()
            assert len(stored) == 109  # 110 returned across pages, one duplicate info hash.
            by_title = {release.title: release for release in stored}
            assert by_title["Show.S01E01.1080p"].episode_number == 1
            assert by_title["Show.S01.1080p"].season_number == 1
            assert by_title["Show.S01-S02.1080p"].season_coverage == "1,2"
            assert by_title["Show.S01E03.REJECT.1080p"].passed_rules is False
            assert by_title["Show.S01E03.REJECT.1080p"].rejection_reason is not None

            episodes = (await db.execute(select(Episode))).scalars().all()
            assert {episode.status for episode in episodes} == {RequestStatus.STAGED}

            details = await DetailService(db).load_request_details(
                request,
                request_id=request.id,
                background_tasks=BackgroundTasks(),
                limit=25,
            )
            assert details.total_releases == 109
            assert len(details.releases) == 25
            assert details.tv_info is not None
            assert "1" in details.tv_info.releases_by_season
            assert "2" in details.tv_info.releases_by_season
            assert "1-1" in details.tv_info.releases_by_episode
            assert any(
                release["title"] == "Show.S01-S02.1080p"
                for release in details.tv_info.releases_by_season["2"]
            )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_limited_season_sweep_does_not_trigger_exact_fallback(
    monkeypatch,
) -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    try:
        async with session_maker() as db:
            request = Request(
                external_id="tv-gap",
                media_type=MediaType.TV,
                tmdb_id=999,
                tvdb_id=12345,
                title="Show",
                year=2024,
                status=RequestStatus.PENDING,
            )
            s2 = Season(season_number=2, status=RequestStatus.PENDING)
            s3 = Season(season_number=3, status=RequestStatus.PENDING)
            s2.episodes = [
                Episode(episode_number=1, title="S2E1", air_date=date(2024, 2, 1)),
                Episode(episode_number=2, title="S2E2", air_date=date(2024, 2, 8)),
            ]
            request.seasons = [s2, s3]
            db.add(request)
            await db.commit()

            class FakeStagingService:
                def __init__(self, _db):
                    pass

                async def use_releases(self, _request, _releases, *, selection_source: str):
                    return {"status": "staged", "message": "staged"}

            monkeypatch.setattr(
                "app.siftarr.services.decisions.tv_decision_service.StagingService",
                FakeStagingService,
            )

            prowlarr = FakeCappedEpisodeGapProwlarr()
            service = TVDecisionService(db, prowlarr, QbittorrentService())

            async def fake_rule_engine() -> RejectNamedRuleEngine:
                return RejectNamedRuleEngine()

            monkeypatch.setattr(service, "_get_rule_engine", fake_rule_engine)

            result = await service.process_request(request.id)

            assert result["status"] == "staged"
            assert prowlarr.swept_seasons == [2]
            assert prowlarr.exact_episode_calls == []

            stored = (await db.execute(select(Release))).scalars().all()
            assert {release.title for release in stored} == {"Show.S02E02.1080p"}

            details = await DetailService(db).load_request_details(
                request,
                request_id=request.id,
                background_tasks=BackgroundTasks(),
                limit=25,
            )
            assert details.tv_info is not None
            assert set(details.tv_info.releases_by_episode) == {"2-2"}
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_georgie_sweep_episode_rows_persist_without_exact_fallbacks(
    monkeypatch,
) -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    try:
        async with session_maker() as db:
            request = Request(
                external_id="tv-georgie-capped",
                media_type=MediaType.TV,
                tmdb_id=999,
                tvdb_id=12345,
                title="Georgie & Mandy's First Marriage",
                year=2024,
                status=RequestStatus.PENDING,
            )
            s1 = Season(season_number=1, status=RequestStatus.PENDING)
            s1.episodes = [Episode(episode_number=1, title="S1E1", air_date=date(2024, 1, 1))]
            s2 = Season(season_number=2, status=RequestStatus.PENDING)
            s2.episodes = [
                Episode(episode_number=i, title=f"S2E{i}", air_date=date(2024, 2, 1))
                for i in range(1, 23)
            ]
            request.seasons = [s1, s2]
            db.add(request)
            await db.commit()

            class FakeStagingService:
                def __init__(self, _db):
                    pass

                async def use_releases(self, _request, _releases, *, selection_source: str):
                    return {"status": "staged", "message": "staged"}

            monkeypatch.setattr(
                "app.siftarr.services.decisions.tv_decision_service.StagingService",
                FakeStagingService,
            )

            prowlarr = FakeCappedGeorgieProwlarr()
            service = TVDecisionService(db, prowlarr, QbittorrentService())

            async def fake_rule_engine() -> RejectNamedRuleEngine:
                return RejectNamedRuleEngine()

            monkeypatch.setattr(service, "_get_rule_engine", fake_rule_engine)

            result = await service.process_request(request.id)

            assert result["status"] == "staged"
            assert prowlarr.swept_seasons == [1, 2]
            assert prowlarr.exact_episode_calls == []

            stored = (await db.execute(select(Release))).scalars().all()
            stored_titles = {release.title for release in stored}
            assert "Georgie.And.Mandys.First.Marriage.S02E01.1080p.WEB-DL" not in stored_titles
            for episode in range(12, 19):
                assert (
                    f"Georgie.and.Mandys.First.Marriage.S02E{episode:02d}.1080p.WEB-DL"
                    in stored_titles
                )

            details = await DetailService(db).load_request_details(
                request,
                request_id=request.id,
                background_tasks=BackgroundTasks(),
                limit=2,
            )
            assert details.tv_info is not None
            s2_episode_keys = {
                key for key in details.tv_info.releases_by_episode if key.startswith("2-")
            }
            assert {*(f"2-{episode}" for episode in range(12, 19))} <= s2_episode_keys
    finally:
        await engine.dispose()
