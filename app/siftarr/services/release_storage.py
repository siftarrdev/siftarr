"""Helpers for persisting searched releases."""

import logging

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.siftarr.models.release import Release
from app.siftarr.models.request import Request
from app.siftarr.services.prowlarr_service import ProwlarrRelease
from app.siftarr.services.release_parser import (
    parse_release_coverage,
    parse_season_episode,
    serialize_release_coverage,
)
from app.siftarr.services.rule_engine import ReleaseEvaluation

logger = logging.getLogger(__name__)


def get_release_persistence_key(*, title: str, info_hash: str | None) -> str:
    """Return the stable key used when deduplicating persisted releases."""
    return info_hash or title


async def _purge_releases(
    db: AsyncSession,
    *,
    request_id: int | None = None,
    commit: bool = True,
) -> dict[str, int]:
    """Delete stored releases, optionally for one request."""
    count_stmt = select(func.count()).select_from(Release)
    release_delete_query = delete(Release)
    if request_id is not None:
        count_stmt = count_stmt.where(Release.request_id == request_id)
        release_delete_query = release_delete_query.where(Release.request_id == request_id)

    count_result = await db.scalar(count_stmt)
    deleted_release_count = count_result or 0
    await db.execute(release_delete_query)
    if commit:
        await db.commit()

    return {"deleted_releases": deleted_release_count}


async def clear_release_search_cache(db: AsyncSession) -> dict[str, int]:
    """Clear persisted release search cache."""
    result = await _purge_releases(db)

    logger.info(
        "Cleared persisted release search cache: deleted_releases=%s",
        result["deleted_releases"],
    )
    return result


def build_prowlarr_release(release: Release) -> ProwlarrRelease:
    """Rebuild a Prowlarr release object from a stored search result."""
    return ProwlarrRelease(
        title=release.title,
        size=release.size,
        seeders=release.seeders,
        leechers=release.leechers,
        download_url=release.download_url,
        magnet_url=release.magnet_url,
        info_hash=release.info_hash,
        indexer=release.indexer,
        publish_date=release.publish_date,
        resolution=release.resolution,
        codec=release.codec,
        release_group=release.release_group,
    )


async def store_search_results(
    db: AsyncSession,
    request_id: int,
    evaluations: list[ReleaseEvaluation],
) -> dict[str, Release]:
    """Replace stored search results for a request with the latest evaluations."""
    await _purge_releases(db, request_id=request_id, commit=False)

    records_by_key: dict[str, Release] = {}
    seen_keys: set[str] = set()
    for evaluation in evaluations:
        release = evaluation.release
        dedupe_key = get_release_persistence_key(title=release.title, info_hash=release.info_hash)
        if dedupe_key in seen_keys:
            continue
        seen_keys.add(dedupe_key)

        parsed = parse_season_episode(release.title)
        coverage = parse_release_coverage(release.title)
        record = Release(
            request_id=request_id,
            title=release.title,
            size=release.size,
            seeders=release.seeders,
            leechers=release.leechers,
            download_url=release.download_url,
            magnet_url=release.magnet_url,
            info_hash=release.info_hash,
            indexer=release.indexer,
            publish_date=release.publish_date,
            resolution=release.resolution,
            codec=release.codec,
            release_group=release.release_group,
            season_number=parsed.season_number,
            episode_number=parsed.episode_number,
            season_coverage=serialize_release_coverage(coverage),
            score=evaluation.total_score,
            passed_rules=evaluation.passed,
            rejection_reason=evaluation.rejection_reason[:500]
            if evaluation.rejection_reason
            else None,
        )
        db.add(record)
        records_by_key[dedupe_key] = record

    await db.commit()
    for record in records_by_key.values():
        await db.refresh(record)
    return records_by_key


async def persist_manual_release(
    db: AsyncSession,
    request: Request,
    release: ProwlarrRelease,
    evaluation: ReleaseEvaluation,
) -> Release:
    """Persist or update a manually selected release for reuse by selection flows."""
    if not (release.magnet_url or release.download_url):
        raise RuntimeError(f"Release '{release.title}' has no usable download source.")

    parsed = parse_season_episode(release.title)
    coverage = parse_release_coverage(release.title)

    if release.info_hash:
        filters = [Release.request_id == request.id, Release.info_hash == release.info_hash]
    else:
        filters = [
            Release.request_id == request.id,
            Release.title == release.title,
            Release.info_hash.is_(None),
        ]

    existing_result = await db.execute(select(Release).where(*filters))
    record = existing_result.scalar_one_or_none()

    if record is None:
        record = Release(
            request_id=request.id,
            title=release.title,
            size=release.size,
            seeders=release.seeders,
            leechers=release.leechers,
            download_url=release.download_url,
            magnet_url=release.magnet_url,
            info_hash=release.info_hash,
            indexer=release.indexer,
            publish_date=release.publish_date,
            resolution=release.resolution,
            codec=release.codec,
            release_group=release.release_group,
            season_number=parsed.season_number,
            episode_number=parsed.episode_number,
            season_coverage=serialize_release_coverage(coverage),
            score=evaluation.total_score,
            passed_rules=evaluation.passed,
            rejection_reason=evaluation.rejection_reason[:500]
            if evaluation.rejection_reason
            else None,
        )
        db.add(record)
    else:
        record.size = release.size
        record.seeders = release.seeders
        record.leechers = release.leechers
        record.download_url = release.download_url
        record.magnet_url = release.magnet_url
        record.info_hash = release.info_hash
        record.indexer = release.indexer
        record.publish_date = release.publish_date
        record.resolution = release.resolution
        record.codec = release.codec
        record.release_group = release.release_group
        record.season_number = parsed.season_number
        record.episode_number = parsed.episode_number
        record.season_coverage = serialize_release_coverage(coverage)
        record.score = evaluation.total_score
        record.passed_rules = evaluation.passed
        record.rejection_reason = (
            evaluation.rejection_reason[:500] if evaluation.rejection_reason else None
        )

    await db.commit()
    await db.refresh(record)
    return record
