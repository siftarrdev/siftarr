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
        self.exact_episode_calls: list[tuple[int, int]] = []
        self.pack_calls = 0

    async def search_tv_episode_exact(
        self,
        title: str,
        season: int,
        episode: int,
        categories: list[int] | None = None,
        cacheable: bool = True,
        request_id: int | None = None,
        progress_callback=None,
    ) -> ProwlarrSearchResult:
        self.exact_episode_calls.append((season, episode))
        releases_by_episode = {
            (1, 1): [_release("Show.S01E01.1080p", 1, info_hash="exact-s01e01")],
            (1, 2): [_release("Show.S01E02.1080p", 2, info_hash="exact-s01e02")],
            (1, 3): [_release("Show.S01E03.REJECT.1080p", 3, info_hash="rejected-s01e03")],
            (2, 1): [_release("Show.S02E01.1080p", 4, info_hash="exact-s02e01")],
        }
        return ProwlarrSearchResult(
            releases=releases_by_episode.get((season, episode), []),
            query_time_ms=10,
        )

    async def search_tv_packs_broad(
        self,
        title: str,
        categories: list[int] | None = None,
        cacheable: bool = True,
        request_id: int | None = None,
        progress_callback=None,
    ) -> ProwlarrSearchResult:
        self.pack_calls += 1
        releases = [
            _release("Show.S01E01.1080p", 101, info_hash="exact-s01e01"),
            _release("Show.S01.1080p", 102, info_hash="season-s01"),
            _release("Show.S01-S02.1080p", 103, info_hash="multi-s01-s02"),
        ]
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
        cancellation_check=None,
    ) -> ProwlarrSearchResult:
        self.swept_seasons.append(season)
        return ProwlarrSearchResult(
            releases=[_release("Show.S02E02.1080p", 2, info_hash="s02e02-sweep")],
            query_time_ms=10,
            hit_limit=True,
        )

    async def search_tv_episode_exact(
        self,
        title: str,
        season: int,
        episode: int,
        categories: list[int] | None = None,
        cacheable: bool = True,
        request_id: int | None = None,
        progress_callback=None,
    ) -> ProwlarrSearchResult:
        self.exact_episode_calls.append((season, episode))
        releases = []
        if (season, episode) == (2, 2):
            releases = [_release("Show.S02E02.1080p", 2, info_hash="s02e02-exact")]
        return ProwlarrSearchResult(releases=releases, query_time_ms=10)


class FakePartialSeasonPackProwlarr(ProwlarrService):
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
        cancellation_check=None,
    ) -> ProwlarrSearchResult:
        self.swept_seasons.append(season)
        return ProwlarrSearchResult(
            releases=[_release("Show.S01.1080p", 10, info_hash="season-s01")],
            query_time_ms=10,
        )

    async def search_tv_episode_exact(
        self,
        title: str,
        season: int,
        episode: int,
        categories: list[int] | None = None,
        cacheable: bool = True,
        request_id: int | None = None,
        progress_callback=None,
    ) -> ProwlarrSearchResult:
        self.exact_episode_calls.append((season, episode))
        return ProwlarrSearchResult(
            releases=[_release("Show.S01E02.1080p", 11, info_hash="exact-s01e02")],
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
        cancellation_check=None,
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

    async def search_tv_episode_exact(
        self,
        title: str,
        season: int,
        episode: int,
        categories: list[int] | None = None,
        cacheable: bool = True,
        request_id: int | None = None,
        progress_callback=None,
    ) -> ProwlarrSearchResult:
        self.exact_episode_calls.append((season, episode))
        if season == 1 and episode == 1:
            return ProwlarrSearchResult(
                releases=[
                    _release(
                        "Georgie.And.Mandys.First.Marriage.S01E01.1080p.WEB-DL",
                        1,
                        info_hash="georgie-s01e01-exact",
                    )
                ],
                query_time_ms=10,
            )
        if season == 2 and 12 <= episode <= 18:
            return ProwlarrSearchResult(
                releases=[
                    _release(
                        f"Georgie.and.Mandys.First.Marriage.S02E{episode:02d}.1080p.WEB-DL",
                        episode,
                        info_hash=f"georgie-s02e{episode:02d}-exact",
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

            result = await service.process_request(request.id, search_mode="full")

            assert result["status"] == "staged"
            assert prowlarr.exact_episode_calls == [(1, 1), (1, 2), (1, 3), (2, 1)]
            assert prowlarr.pack_calls == 1

            stored = (await db.execute(select(Release))).scalars().all()
            assert len(stored) == 6  # Exact episodes plus broad pack results, with one duplicate.
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
            assert details.total_releases == 6
            assert len(details.releases) == 6
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
async def test_partly_available_season_skips_pack_and_selects_missing_episode(
    monkeypatch,
) -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    try:
        async with session_maker() as db:
            request = Request(
                external_id="tv-partial-pack",
                media_type=MediaType.TV,
                tmdb_id=999,
                tvdb_id=12345,
                title="Show",
                year=2024,
                status=RequestStatus.PENDING,
            )
            season = Season(season_number=1, status=RequestStatus.PENDING)
            season.episodes = [
                Episode(
                    episode_number=1,
                    title="S1E1",
                    air_date=date(2024, 1, 1),
                    status=RequestStatus.COMPLETED,
                ),
                Episode(episode_number=2, title="S1E2", air_date=date(2024, 1, 2)),
            ]
            request.seasons = [season]
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

            prowlarr = FakePartialSeasonPackProwlarr()
            service = TVDecisionService(db, prowlarr, QbittorrentService())

            async def fake_rule_engine() -> RejectNamedRuleEngine:
                return RejectNamedRuleEngine()

            monkeypatch.setattr(service, "_get_rule_engine", fake_rule_engine)

            result = await service.process_request(request.id)

            assert result["status"] == "staged"
            assert [release["title"] for release in result["selected_releases"]] == [
                "Show.S01E02.1080p"
            ]
            assert prowlarr.swept_seasons == []
            assert prowlarr.exact_episode_calls == [(1, 2)]

            stored = (await db.execute(select(Release))).scalars().all()
            assert {release.title for release in stored} == {"Show.S01E02.1080p"}

            episodes = (await db.execute(select(Episode))).scalars().all()
            statuses = {episode.episode_number: episode.status for episode in episodes}
            assert statuses == {1: RequestStatus.COMPLETED, 2: RequestStatus.STAGED}
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_fully_missing_season_pack_selected_without_exact_fallback(monkeypatch) -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    try:
        async with session_maker() as db:
            request = Request(
                external_id="tv-missing-pack",
                media_type=MediaType.TV,
                tmdb_id=999,
                tvdb_id=12345,
                title="Show",
                year=2024,
                status=RequestStatus.PENDING,
            )
            season = Season(season_number=1, status=RequestStatus.PENDING)
            season.episodes = [
                Episode(episode_number=1, title="S1E1", air_date=date(2024, 1, 1)),
                Episode(episode_number=2, title="S1E2", air_date=date(2024, 1, 2)),
            ]
            request.seasons = [season]
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

            prowlarr = FakePartialSeasonPackProwlarr()
            service = TVDecisionService(db, prowlarr, QbittorrentService())

            async def fake_rule_engine() -> RejectNamedRuleEngine:
                return RejectNamedRuleEngine()

            monkeypatch.setattr(service, "_get_rule_engine", fake_rule_engine)

            result = await service.process_request(request.id)

            assert [release["title"] for release in result["selected_releases"]] == [
                "Show.S01.1080p"
            ]
            assert prowlarr.swept_seasons == [1]
            assert prowlarr.exact_episode_calls == []

            episodes = (await db.execute(select(Episode))).scalars().all()
            assert {episode.status for episode in episodes} == {RequestStatus.STAGED}
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
            assert prowlarr.exact_episode_calls == [(2, 1), (2, 2)]

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
            assert prowlarr.exact_episode_calls == [
                (1, 1),
                *((2, episode) for episode in range(1, 23)),
            ]

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
            assert details.filtered_total_releases > len(details.releases)
            visible_release_titles = {release["title"] for release in details.releases}
            visible_bucket_titles = {
                release["title"]
                for bucket in details.tv_info.releases_by_episode.values()
                for release in bucket
            }
            # TV buckets use the complete filtered result set, not the generic
            # paginated release page, so season/pack rows remain visible.
            assert visible_release_titles <= visible_bucket_titles
    finally:
        await engine.dispose()
