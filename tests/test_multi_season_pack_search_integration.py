"""Integration-style validation for the multi-season pack search path."""

from datetime import date

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.siftarr.config import Settings
from app.siftarr.models import Base
from app.siftarr.models.episode import Episode
from app.siftarr.models.release import Release
from app.siftarr.models.request import MediaType, Request, RequestStatus
from app.siftarr.models.season import Season
from app.siftarr.services.dashboard import search_service
from app.siftarr.services.dashboard.search_service import SearchService
from app.siftarr.services.decisions.rule_engine import ReleaseEvaluation, RuleEngine
from app.siftarr.services.integrations.prowlarr_service import (
    ProwlarrRelease,
    ProwlarrSearchResult,
    ProwlarrService,
)


def _release(title: str, info_hash: str) -> ProwlarrRelease:
    return ProwlarrRelease(
        title=title,
        size=8 * 1024 * 1024 * 1024,
        seeders=50,
        leechers=1,
        download_url=f"https://ipt.example/{info_hash}",
        info_hash=info_hash,
        indexer="IPTorrents",
    )


class FakePackProwlarr(ProwlarrService):
    def __init__(self) -> None:
        super().__init__(Settings())
        self.swept_seasons: list[int] = []
        self.broad_calls = 0

    async def search_tv_season_sweep(
        self,
        title: str,
        season: int,
        imdbid=None,
        tvdbid=None,
        categories=None,
        cacheable: bool = True,
        request_id=None,
        progress_callback=None,
    ) -> ProwlarrSearchResult:
        self.swept_seasons.append(season)
        return ProwlarrSearchResult(
            releases=[_release(f"Show.S{season:02d}.1080p", f"season-s{season:02d}")],
            query_time_ms=5,
        )

    async def search_tv_packs_broad(
        self,
        title: str,
        categories=None,
        cacheable: bool = True,
        request_id=None,
        progress_callback=None,
    ) -> ProwlarrSearchResult:
        self.broad_calls += 1
        return ProwlarrSearchResult(
            releases=[
                _release("Show.S01-S03.1080p.WEB-DL", "multi-s01-s03"),
                _release("Show.Complete.Series.1080p.BluRay", "complete-series"),
            ],
            query_time_ms=5,
        )


class PassAllRuleEngine(RuleEngine):
    def evaluate(self, release: ProwlarrRelease) -> ReleaseEvaluation:
        return ReleaseEvaluation(
            release=release,
            passed=True,
            total_score=100,
            matches=[],
            rejection_reason=None,
        )


@pytest.mark.asyncio
async def test_multi_season_search_persists_packs_against_real_session(monkeypatch) -> None:
    """The multi-season sweep must survive real ORM persistence across seasons."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    try:
        async with session_maker() as db:
            request = Request(
                external_id="tv-multi-1",
                media_type=MediaType.TV,
                tmdb_id=555,
                tvdb_id=12345,
                title="Show",
                year=2024,
                status=RequestStatus.PENDING,
            )
            request.seasons = [
                Season(
                    season_number=number,
                    status=RequestStatus.PENDING,
                    episodes=[
                        Episode(
                            episode_number=1,
                            title=f"S{number}E1",
                            air_date=date(2024, number, 1),
                        )
                    ],
                )
                for number in (1, 2, 3)
            ]
            db.add(request)
            await db.commit()

            prowlarr = FakePackProwlarr()
            monkeypatch.setattr(search_service, "ProwlarrService", lambda settings: prowlarr)
            monkeypatch.setattr(
                search_service.RuleEngine,
                "from_db_rules",
                staticmethod(lambda *args, **kwargs: PassAllRuleEngine([])),
            )

            service = SearchService(db)

            async def no_imdb(_request):
                return None

            monkeypatch.setattr(service, "_load_imdb_id", no_imdb)

            data = await service.search_multi_season_packs(request, request_id=request.id)

            assert prowlarr.broad_calls == 1
            assert prowlarr.swept_seasons == [1, 2, 3]
            titles = [release["title"] for release in data.releases]
            assert "Show.S01-S03.1080p.WEB-DL" in titles
            assert "Show.Complete.Series.1080p.BluRay" in titles
            assert "Show.S01.1080p" not in titles

            stored = (await db.execute(select(Release))).scalars().all()
            stored_titles = {release.title for release in stored}
            assert "Show.S01-S03.1080p.WEB-DL" in stored_titles
            assert "Show.Complete.Series.1080p.BluRay" in stored_titles
    finally:
        await engine.dispose()
