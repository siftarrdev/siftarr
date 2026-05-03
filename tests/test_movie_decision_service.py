from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.siftarr.models._base import Base
from app.siftarr.models.release import Release
from app.siftarr.models.request import MediaType, Request, RequestStatus
from app.siftarr.services.movie_decision_service import MovieDecisionService
from app.siftarr.services.prowlarr_service import ProwlarrRelease, ProwlarrSearchResult


@pytest.mark.asyncio
async def test_movie_identity_mismatch_is_persisted_and_not_selected(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_maker = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    staged_calls = []

    async def fake_use_releases(*args, **kwargs):
        staged_calls.append((args, kwargs))
        return {"status": "staged", "message": "staged"}

    monkeypatch.setattr(
        "app.siftarr.services.movie_decision_service.use_releases",
        fake_use_releases,
    )

    async with session_maker() as session:
        request = Request(
            external_id="movie-32293",
            media_type=MediaType.MOVIE,
            title="The Cheetah Girls",
            year=2003,
            tmdb_id=32293,
            status=RequestStatus.PENDING,
        )
        session.add(request)
        await session.commit()
        await session.refresh(request)

        prowlarr = AsyncMock()
        prowlarr.search_by_tmdbid.return_value = ProwlarrSearchResult(
            releases=[
                ProwlarrRelease(
                    title="The.Cheetah.Girls.2.2005.1080p.WEB-DL",
                    size=1024,
                    seeders=10,
                    leechers=0,
                    download_url="https://example.test/bad.torrent",
                    indexer="IndexerA",
                )
            ],
            query_time_ms=10,
        )

        result = await MovieDecisionService(session, prowlarr, AsyncMock()).process_request(
            request.id
        )

        assert result["status"] == "pending"
        assert staged_calls == []

        stored = (await session.execute(select(Release))).scalar_one()
        assert stored.passed_rules is False
        assert stored.rejection_reason is not None
        assert "release title 'The.Cheetah.Girls.2'" in stored.rejection_reason

        refreshed_request = await session.get(Request, request.id)
        assert refreshed_request is not None
        assert refreshed_request.status == RequestStatus.PENDING
        assert refreshed_request.rejection_reason is not None
        assert "Movie identity mismatch" in refreshed_request.rejection_reason

    await engine.dispose()


@pytest.mark.asyncio
async def test_movie_identity_filter_allows_exact_title_missing_release_year(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_maker = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async def fake_use_releases(db, request, releases, selection_source):
        assert releases[0].title == "The.Cheetah.Girls.1080p.WEB-DL"
        return {"status": "staged", "message": "staged"}

    monkeypatch.setattr(
        "app.siftarr.services.movie_decision_service.use_releases",
        fake_use_releases,
    )

    async with session_maker() as session:
        request = Request(
            external_id="movie-32293",
            media_type=MediaType.MOVIE,
            title="The Cheetah Girls",
            year=2003,
            tmdb_id=32293,
            status=RequestStatus.PENDING,
        )
        session.add(request)
        await session.commit()
        await session.refresh(request)

        prowlarr = AsyncMock()
        prowlarr.search_by_tmdbid.return_value = ProwlarrSearchResult(
            releases=[
                ProwlarrRelease(
                    title="The.Cheetah.Girls.1080p.WEB-DL",
                    size=1024,
                    seeders=10,
                    leechers=0,
                    download_url="https://example.test/good.torrent",
                    indexer="IndexerA",
                )
            ],
            query_time_ms=10,
        )

        result = await MovieDecisionService(session, prowlarr, AsyncMock()).process_request(
            request.id
        )

        assert result["status"] == "staged"
        stored = (await session.execute(select(Release))).scalar_one()
        assert stored.passed_rules is True
        assert stored.rejection_reason is None

    await engine.dispose()
