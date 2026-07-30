"""Tests for persisted release upsert/purge behaviour."""

from typing import cast

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.siftarr.models._base import Base
from app.siftarr.models.release import Release
from app.siftarr.services.decisions.rule_engine import ReleaseEvaluation
from app.siftarr.services.decisions.tv_decision_service import TVDecisionService
from app.siftarr.services.integrations.prowlarr_service import ProwlarrRelease, ProwlarrService
from app.siftarr.services.integrations.qbittorrent_service import QbittorrentService
from app.siftarr.services.releases.release_storage import store_search_results

REQUEST_ID = 1
EPISODE_TITLE = "Show.S01E01.1080p.WEB-DL-GRP"
PACK_TITLE = "Show.S01.1080p.WEB-DL-GRP"


@pytest.fixture
async def db_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    async with session_maker() as session:
        yield session
    await engine.dispose()


def _evaluation(title: str, *, info_hash: str | None = None, score: int = 10) -> ReleaseEvaluation:
    release = ProwlarrRelease(
        title=title,
        size=1000,
        seeders=5,
        leechers=1,
        download_url="https://example.test/download",
        info_hash=info_hash,
        indexer="Idx",
        resolution="1080p",
    )
    return ReleaseEvaluation(release=release, passed=True, total_score=score, matches=[])


async def _titles(db_session, title: str) -> list[Release]:
    result = await db_session.execute(
        select(Release).where(Release.request_id == REQUEST_ID, Release.title == title)
    )
    return list(result.scalars().all())


@pytest.mark.asyncio
async def test_pack_search_does_not_duplicate_release_stored_by_other_source(db_session):
    await store_search_results(
        db_session,
        REQUEST_ID,
        [_evaluation(EPISODE_TITLE, score=10)],
        scope={"type": "single_episode", "season_number": 1, "episode_number": 1},
    )

    await store_search_results(
        db_session,
        REQUEST_ID,
        [_evaluation(EPISODE_TITLE, score=42), _evaluation(PACK_TITLE)],
        scope={"type": "season_packs", "season_number": 1},
        source="adhoc",
    )

    rows = await _titles(db_session, EPISODE_TITLE)
    assert len(rows) == 1
    assert rows[0].score == 42
    assert rows[0].search_source == "adhoc"
    assert len(await _titles(db_session, PACK_TITLE)) == 1


@pytest.mark.asyncio
async def test_live_result_notification_follows_a_separate_session_visible_commit(db_session):
    """The SSE notification must not race a details read on another session."""
    session_maker = async_sessionmaker(db_session.bind, expire_on_commit=False)
    events: list[dict] = []

    async def progress(payload: dict) -> None:
        async with session_maker() as reader:
            result = await reader.execute(select(Release).where(Release.request_id == REQUEST_ID))
            assert [row.title for row in result.scalars().all()] == [PACK_TITLE]
        events.append(payload)

    service = TVDecisionService(
        db_session, cast(ProwlarrService, object()), cast(QbittorrentService, object())
    )
    await service._publish_result_batch(REQUEST_ID, [_evaluation(PACK_TITLE)], 1, progress)

    assert events == [{"phase": "results_updated", "request_id": REQUEST_ID, "changed_count": 1}]


@pytest.mark.asyncio
async def test_live_result_batch_does_not_purge_prior_cache_rows(db_session):
    await store_search_results(db_session, REQUEST_ID, [_evaluation(EPISODE_TITLE)])
    service = TVDecisionService(
        db_session, cast(ProwlarrService, object()), cast(QbittorrentService, object())
    )

    await service._publish_result_batch(REQUEST_ID, [_evaluation(PACK_TITLE)], 1, None)

    assert len(await _titles(db_session, EPISODE_TITLE)) == 1
    assert len(await _titles(db_session, PACK_TITLE)) == 1


@pytest.mark.asyncio
async def test_scoped_purge_only_removes_stale_rows_in_scope(db_session):
    await store_search_results(
        db_session,
        REQUEST_ID,
        [_evaluation(EPISODE_TITLE)],
        scope={"type": "single_episode", "season_number": 1, "episode_number": 1},
    )
    await store_search_results(
        db_session,
        REQUEST_ID,
        [_evaluation(PACK_TITLE)],
        scope={"type": "season_packs", "season_number": 1},
    )

    # A fresh season-pack search that no longer returns the old pack purges it,
    # but must not touch the out-of-scope episode row.
    await store_search_results(
        db_session,
        REQUEST_ID,
        [_evaluation("Show.S01.2160p.WEB-DL-GRP")],
        scope={"type": "season_packs", "season_number": 1},
    )

    assert len(await _titles(db_session, EPISODE_TITLE)) == 1
    assert await _titles(db_session, PACK_TITLE) == []
    assert len(await _titles(db_session, "Show.S01.2160p.WEB-DL-GRP")) == 1


@pytest.mark.asyncio
async def test_duplicate_rows_from_dirty_data_are_collapsed(db_session):
    for source in ("automatic", "adhoc"):
        db_session.add(
            Release(
                request_id=REQUEST_ID,
                title=EPISODE_TITLE,
                size=1,
                seeders=1,
                leechers=0,
                download_url="https://example.test/download",
                indexer="Idx",
                score=1,
                passed_rules=True,
                season_number=1,
                episode_number=1,
                search_source=source,
            )
        )
    await db_session.commit()

    await store_search_results(
        db_session,
        REQUEST_ID,
        [_evaluation(EPISODE_TITLE, score=77)],
        scope={"type": "single_episode", "season_number": 1, "episode_number": 1},
        source="adhoc",
    )

    rows = await _titles(db_session, EPISODE_TITLE)
    assert len(rows) == 1
    assert rows[0].score == 77
