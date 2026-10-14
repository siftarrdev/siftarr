from sqlalchemy import select

from app.siftarr.models.episode import Episode
from app.siftarr.models.release import Release
from app.siftarr.models.request import MediaType
from app.siftarr.services.decisions.rule_engine import ReleaseEvaluation
from app.siftarr.services.integrations.prowlarr_service import ProwlarrRelease
from app.siftarr.services.releases import release_storage
from tests.factories import make_release, make_request, make_season


def _evaluation(title: str, score: int = 20) -> ReleaseEvaluation:
    release = ProwlarrRelease(
        title=title,
        size=2,
        seeders=20,
        leechers=0,
        download_url=f"https://example.test/{title}.torrent",
        indexer="IndexerNew",
    )
    return ReleaseEvaluation(release=release, passed=True, total_score=score, matches=[])


async def _titles(db, request) -> list[str]:
    rows = await db.execute(
        select(Release.title).where(Release.request_id == request.id).order_by(Release.title)
    )
    return list(rows.scalars())


async def test_clear_release_search_cache_deletes_releases_and_keeps_episodes(db):
    request = await make_request(db, media_type=MediaType.TV, title="Foundation")
    await make_season(db, request, 1, episodes=2)
    await make_release(db, request, title="Foundation.S01.1080p.WEB-DL")
    await db.commit()

    result = await release_storage.clear_release_search_cache(db)

    assert result == {"deleted_releases": 1}
    assert (await db.execute(select(Release))).scalars().all() == []
    assert len((await db.execute(select(Episode))).scalars().all()) == 2


async def test_store_search_results_replaces_request_releases(db):
    request = await make_request(db, media_type=MediaType.TV, title="Severance")
    await make_release(db, request, title="Severance.S01E01.720p.WEB-DL")
    await db.commit()

    stored = await release_storage.store_search_results(
        db, request.id, [_evaluation("Severance.S01E01.1080p.WEB-DL", 15)]
    )

    assert list(stored) == ["Severance.S01E01.1080p.WEB-DL"]
    assert await _titles(db, request) == ["Severance.S01E01.1080p.WEB-DL"]


async def test_store_search_results_purges_only_matching_tv_scope(db):
    request = await make_request(db, media_type=MediaType.TV, title="Severance")
    await make_release(
        db, request, title="Severance.S01E01.720p.WEB-DL", season_number=1, episode_number=1
    )
    await make_release(
        db, request, title="Severance.S01.1080p.WEB-DL", season_number=1, season_coverage="1"
    )
    await db.commit()

    await release_storage.store_search_results(
        db,
        request.id,
        [_evaluation("Severance.S01E01.2160p.WEB-DL")],
        scope={"type": "single_episode", "season_number": 1, "episode_number": 1},
    )

    assert await _titles(db, request) == [
        "Severance.S01.1080p.WEB-DL",
        "Severance.S01E01.2160p.WEB-DL",
    ]


async def test_automatic_store_search_results_preserves_adhoc_and_manual_releases(db):
    request = await make_request(db, media_type=MediaType.TV, title="Severance")
    for name, source in (("adhoc", "adhoc"), ("manual", "manual"), ("old-auto", "automatic")):
        await make_release(
            db, request, title=f"Severance.S01.{name}.1080p.WEB-DL", search_source=source
        )
    await db.commit()

    await release_storage.store_search_results(
        db, request.id, [_evaluation("Severance.S01.new-auto.1080p.WEB-DL")]
    )

    assert await _titles(db, request) == [
        "Severance.S01.adhoc.1080p.WEB-DL",
        "Severance.S01.manual.1080p.WEB-DL",
        "Severance.S01.new-auto.1080p.WEB-DL",
    ]
