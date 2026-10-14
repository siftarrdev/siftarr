"""Integration-style validation for the multi-season pack search path."""

from sqlalchemy import select

from app.siftarr.config import Settings
from app.siftarr.models.release import Release
from app.siftarr.models.request import MediaType
from app.siftarr.services.dashboard import search_service
from app.siftarr.services.dashboard.search_service import SearchService
from app.siftarr.services.decisions.rule_engine import ReleaseEvaluation, RuleEngine
from app.siftarr.services.integrations.prowlarr_service import (
    ProwlarrRelease,
    ProwlarrSearchResult,
    ProwlarrService,
)
from tests.factories import make_request, make_season


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
        categories=None,
        cacheable: bool = True,
        request_id=None,
        progress_callback=None,
        cancellation_check=None,
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
    def evaluate(
        self, release: ProwlarrRelease, *, allow_episode_size_fallback: bool = False
    ) -> ReleaseEvaluation:
        return ReleaseEvaluation(
            release=release,
            passed=True,
            total_score=100,
            matches=[],
            rejection_reason=None,
        )


async def test_multi_season_search_persists_packs_against_real_session(db, monkeypatch) -> None:
    """The multi-season sweep must survive real ORM persistence across seasons."""
    request = await make_request(db, media_type=MediaType.TV, title="Show", year=2024)
    for number in (1, 2, 3):
        await make_season(db, request, number, episodes=1)

    prowlarr = FakePackProwlarr()
    monkeypatch.setattr(search_service, "ProwlarrService", lambda settings: prowlarr)
    monkeypatch.setattr(
        search_service.RuleEngine,
        "from_db_rules",
        staticmethod(lambda *args, **kwargs: PassAllRuleEngine([])),
    )
    service = SearchService(db)

    data = await service.search_multi_season_packs(request, request_id=request.id)

    assert prowlarr.broad_calls == 1
    assert prowlarr.swept_seasons == [1, 2, 3]
    titles = [release["title"] for release in data.releases]
    assert "Show.S01-S03.1080p.WEB-DL" in titles
    assert "Show.Complete.Series.1080p.BluRay" in titles
    assert "Show.S01.1080p" not in titles

    stored_titles = {r.title for r in (await db.execute(select(Release))).scalars()}
    assert {"Show.S01-S03.1080p.WEB-DL", "Show.Complete.Series.1080p.BluRay"} <= stored_titles
