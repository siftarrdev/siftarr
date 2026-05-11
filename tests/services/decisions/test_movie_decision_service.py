from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.siftarr.models._base import Base
from app.siftarr.models.release import Release
from app.siftarr.models.request import MediaType, Request, RequestStatus
from app.siftarr.models.rule import Rule, RuleType
from app.siftarr.models.staged_torrent import StagedTorrent
from app.siftarr.services.decisions.movie_decision_service import MovieDecisionService
from app.siftarr.services.integrations.prowlarr_service import ProwlarrRelease, ProwlarrSearchResult


@pytest.mark.asyncio
async def test_movie_identity_mismatch_is_persisted_and_not_selected(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_maker = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    staged_calls = []

    class FakeStaging:
        def __init__(self, db):
            self.db = db

        async def use_releases(self, request, releases, *, selection_source="manual"):
            staged_calls.append((request, releases, selection_source))
            return {"status": "staged", "message": "staged"}

    monkeypatch.setattr(
        "app.siftarr.services.decisions.movie_decision_service.StagingService",
        FakeStaging,
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

    class FakeStaging:
        def __init__(self, db):
            self.db = db

        async def use_releases(self, request, releases, *, selection_source="manual"):
            assert selection_source == "rule"
            assert releases[0].title == "The.Cheetah.Girls.1080p.WEB-DL"
            return {"status": "staged", "message": "staged"}

    monkeypatch.setattr(
        "app.siftarr.services.decisions.movie_decision_service.StagingService",
        FakeStaging,
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


@pytest.mark.asyncio
async def test_movie_db_rules_score_and_persist_selected_staged_torrent(monkeypatch, tmp_path):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_maker = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    monkeypatch.setattr(
        "app.siftarr.services.releases.staging_service.STAGING_DIR",
        tmp_path,
    )
    monkeypatch.setattr(
        "app.siftarr.services.releases.staging_service.get_settings",
        lambda: SimpleNamespace(staging_mode_enabled=True),
    )

    async with session_maker() as session:
        request = Request(
            external_id="movie-1",
            media_type=MediaType.MOVIE,
            title="Example Movie",
            year=2024,
            tmdb_id=1,
            status=RequestStatus.PENDING,
        )
        session.add_all(
            [
                request,
                Rule(
                    name="Movie size",
                    rule_type=RuleType.SIZE_LIMIT,
                    media_scope="movie",
                    pattern="size_limit",
                    min_size_gb=1,
                    max_size_gb=10,
                    is_enabled=True,
                ),
                Rule(
                    name="Require 1080p",
                    rule_type=RuleType.REQUIREMENT,
                    media_scope="movie",
                    pattern="1080p",
                    is_enabled=True,
                ),
                Rule(
                    name="Movie x265",
                    rule_type=RuleType.SCORER,
                    media_scope="movie",
                    pattern="x265",
                    score=50,
                    is_enabled=True,
                ),
                Rule(
                    name="Both WEB",
                    rule_type=RuleType.SCORER,
                    media_scope="both",
                    pattern="WEB-DL",
                    score=5,
                    is_enabled=True,
                ),
            ]
        )
        await session.commit()
        await session.refresh(request)

        prowlarr = AsyncMock()
        prowlarr.search_by_tmdbid.return_value = ProwlarrSearchResult(
            releases=[
                ProwlarrRelease(
                    title="Example.Movie.2024.1080p.WEB-DL.x264-GRP",
                    size=2 * 1024 * 1024 * 1024,
                    seeders=20,
                    leechers=0,
                    download_url="https://example.test/x264.torrent",
                    indexer="IndexerB",
                ),
                ProwlarrRelease(
                    title="Example.Movie.2024.1080p.WEB-DL.x265-GRP",
                    size=2 * 1024 * 1024 * 1024,
                    seeders=10,
                    leechers=0,
                    download_url="https://example.test/x265.torrent",
                    indexer="IndexerA",
                ),
            ],
            query_time_ms=10,
        )

        result = await MovieDecisionService(session, prowlarr, AsyncMock()).process_request(
            request.id
        )

        assert result["status"] == "staged"
        assert result["selected_release"]["title"].endswith("x265-GRP")
        assert result["selected_release"]["score"] == 55

        releases = (await session.execute(select(Release))).scalars().all()
        scores_by_title = {release.title: release.score for release in releases}
        assert scores_by_title["Example.Movie.2024.1080p.WEB-DL.x264-GRP"] == 5
        assert scores_by_title["Example.Movie.2024.1080p.WEB-DL.x265-GRP"] == 55

        staged = (await session.execute(select(StagedTorrent))).scalar_one()
        assert staged.title == "Example.Movie.2024.1080p.WEB-DL.x265-GRP"
        assert staged.score == 55
        assert staged.selection_source == "rule"

    await engine.dispose()


@pytest.mark.asyncio
async def test_movie_best_release_tie_prefers_seeders_not_input_order(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_maker = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    selected_titles = []

    class FakeStaging:
        def __init__(self, db):
            self.db = db

        async def use_releases(self, request, releases, *, selection_source="manual"):
            selected_titles.append(releases[0].title)
            return {"status": "staged", "message": "staged"}

    monkeypatch.setattr(
        "app.siftarr.services.decisions.movie_decision_service.StagingService",
        FakeStaging,
    )

    async with session_maker() as session:
        request = Request(
            external_id="movie-2",
            media_type=MediaType.MOVIE,
            title="Tie Movie",
            year=2024,
            tmdb_id=2,
            status=RequestStatus.PENDING,
        )
        session.add(request)
        await session.commit()
        await session.refresh(request)

        prowlarr = AsyncMock()
        prowlarr.search_by_tmdbid.return_value = ProwlarrSearchResult(
            releases=[
                ProwlarrRelease(
                    title="Tie.Movie.2024.1080p.WEB-DL.LowSeed",
                    size=1024,
                    seeders=1,
                    leechers=0,
                    download_url="https://example.test/low.torrent",
                    indexer="IndexerB",
                ),
                ProwlarrRelease(
                    title="Tie.Movie.2024.1080p.WEB-DL.HighSeed",
                    size=1024,
                    seeders=50,
                    leechers=0,
                    download_url="https://example.test/high.torrent",
                    indexer="IndexerA",
                ),
            ],
            query_time_ms=10,
        )

        result = await MovieDecisionService(session, prowlarr, AsyncMock()).process_request(
            request.id
        )

        assert result["status"] == "staged"
        assert selected_titles == ["Tie.Movie.2024.1080p.WEB-DL.HighSeed"]
        assert result["selected_release"]["score"] == 0

    await engine.dispose()
