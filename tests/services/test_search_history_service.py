from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.siftarr.models import Base
from app.siftarr.models.request import MediaType, Request, RequestStatus
from app.siftarr.models.search_history import SearchRunCandidate
from app.siftarr.services.decisions.rule_engine import ReleaseEvaluation, RuleMatch
from app.siftarr.services.integrations.prowlarr_service import ProwlarrRelease
from app.siftarr.services.search_history_service import SearchHistoryService


async def test_search_history_service_records_compact_candidates():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with session_maker() as db:
        request = Request(
            external_id="movie-1",
            overseerr_request_id=1,
            media_type=MediaType.MOVIE,
            title="Example Movie",
            status=RequestStatus.PENDING,
        )
        db.add(request)
        await db.commit()
        await db.refresh(request)

        release = ProwlarrRelease(
            title="Example.Movie.2026.1080p",
            size=1024 * 1024 * 1024,
            seeders=10,
            leechers=1,
            download_url="https://example.invalid/download",
            indexer="idx",
            resolution="1080p",
        )
        evaluation = ReleaseEvaluation(
            release=release,
            passed=True,
            total_score=15,
            matches=[RuleMatch(1, "1080p", True, 10, "scorer", "score")],
        )

        service = SearchHistoryService(db)
        run = await service.start_run(request.id, trigger="manual", search_mode="movie")
        await service.finalize_run(
            run, evaluations=[evaluation], winners=[evaluation], outcome="staged"
        )

        rows = (await db.execute(select(SearchRunCandidate))).scalars().all()
        assert len(rows) == 1
        assert rows[0].rule_evidence["matches"][0]["rule_name"] == "1080p"
        assert rows[0].summary["title"] == release.title
        assert "download_url" not in rows[0].summary
        assert run.counts == {"total": 1, "passed": 1, "rejected": 0, "staged": 0, "sent": 0}
