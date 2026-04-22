import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.siftarr.models._base import Base
from app.siftarr.models.episode import Episode
from app.siftarr.models.release import Release
from app.siftarr.models.request import MediaType, Request, RequestStatus
from app.siftarr.models.season import Season
from app.siftarr.services import release_storage
from app.siftarr.services.prowlarr_service import ProwlarrRelease
from app.siftarr.services.rule_engine import ReleaseEvaluation


@pytest.mark.asyncio
async def test_clear_release_search_cache_deletes_releases_and_detaches_episode_links():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_maker = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with session_maker() as session:
        request = Request(
            external_id="tv-1",
            media_type=MediaType.TV,
            title="Foundation",
            status=RequestStatus.PENDING,
        )
        season = Season(season_number=1, status=RequestStatus.PENDING, request=request)
        kept_episode = Episode(season=season, episode_number=1, status=RequestStatus.PENDING)
        detached_episode = Episode(season=season, episode_number=2, status=RequestStatus.PENDING)
        release = Release(
            request=request,
            title="Foundation.S01.1080p.WEB-DL",
            size=1,
            seeders=10,
            leechers=1,
            download_url="https://example.test/release.torrent",
            indexer="IndexerA",
            score=10,
            passed_rules=True,
        )

        session.add_all([request, season, kept_episode, detached_episode, release])
        await session.commit()

        result = await release_storage.clear_release_search_cache(session)

        assert result == {"deleted_releases": 1}
        remaining_releases = (await session.execute(select(Release))).scalars().all()
        assert remaining_releases == []

        refreshed_detached_episode = await session.get(Episode, detached_episode.id)
        refreshed_kept_episode = await session.get(Episode, kept_episode.id)
        assert refreshed_detached_episode is not None
        assert refreshed_kept_episode is not None

    await engine.dispose()


@pytest.mark.asyncio
async def test_store_search_results_replaces_request_releases_without_stale_episode_links():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_maker = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with session_maker() as session:
        request = Request(
            external_id="tv-2",
            media_type=MediaType.TV,
            title="Severance",
            status=RequestStatus.PENDING,
        )
        season = Season(season_number=1, status=RequestStatus.PENDING, request=request)
        episode = Episode(season=season, episode_number=1, status=RequestStatus.PENDING)
        old_release = Release(
            request=request,
            title="Severance.S01E01.720p.WEB-DL",
            size=1,
            seeders=8,
            leechers=1,
            download_url="https://example.test/old-release.torrent",
            indexer="IndexerOld",
            score=5,
            passed_rules=True,
        )

        session.add_all([request, season, episode, old_release])
        await session.commit()

        new_release = ProwlarrRelease(
            title="Severance.S01E01.1080p.WEB-DL",
            size=2,
            seeders=20,
            leechers=1,
            download_url="https://example.test/new-release.torrent",
            indexer="IndexerNew",
        )
        evaluation = ReleaseEvaluation(release=new_release, passed=True, total_score=15, matches=[])

        stored_records = await release_storage.store_search_results(
            session,
            request.id,
            [evaluation],
        )

        assert list(stored_records) == ["Severance.S01E01.1080p.WEB-DL"]

        request_releases = (
            (await session.execute(select(Release).where(Release.request_id == request.id)))
            .scalars()
            .all()
        )
        assert len(request_releases) == 1
        assert request_releases[0].title == "Severance.S01E01.1080p.WEB-DL"

    await engine.dispose()
