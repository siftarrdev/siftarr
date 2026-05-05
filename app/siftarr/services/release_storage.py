"""Helpers for persisting searched releases."""

import logging

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.siftarr.models.release import Release
from app.siftarr.models.request import Request
from app.siftarr.services.prowlarr_service import ProwlarrRelease
from app.siftarr.services.release_parser import (
    cached_parse_release_coverage,
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
        files=release.files,
        uploaded_by=release.uploaded_by,
    )


async def store_search_results(
    db: AsyncSession,
    request_id: int,
    evaluations: list[ReleaseEvaluation],
) -> dict[str, Release]:
    """Replace stored search results for a request with the latest evaluations."""
    # 1. Load existing releases for this request
    existing_result = await db.execute(select(Release).where(Release.request_id == request_id))
    existing_records = list(existing_result.scalars().all())

    # Build lookup keyed by persistence key, handle duplicate keys
    existing_by_key: dict[str, Release] = {}
    extra_records: list[Release] = []
    for record in existing_records:
        key = get_release_persistence_key(title=record.title, info_hash=record.info_hash)
        if key in existing_by_key:
            extra_records.append(record)
        else:
            existing_by_key[key] = record

    records_by_key: dict[str, Release] = {}
    seen_keys: set[str] = set()
    matched_keys: set[str] = set()

    for evaluation in evaluations:
        release = evaluation.release
        dedupe_key = get_release_persistence_key(title=release.title, info_hash=release.info_hash)
        if dedupe_key in seen_keys:
            continue
        seen_keys.add(dedupe_key)

        parsed = parse_season_episode(release.title)
        coverage = cached_parse_release_coverage(release.title)

        existing = existing_by_key.get(dedupe_key)
        if existing is not None:
            # UPDATE existing record
            existing.size = release.size
            existing.seeders = release.seeders
            existing.leechers = release.leechers
            existing.download_url = release.download_url
            existing.magnet_url = release.magnet_url
            existing.info_hash = release.info_hash
            existing.indexer = release.indexer
            existing.publish_date = release.publish_date
            existing.resolution = release.resolution
            existing.codec = release.codec
            existing.release_group = release.release_group
            existing.files = release.files
            existing.uploaded_by = release.uploaded_by
            existing.season_number = parsed.season_number
            existing.episode_number = parsed.episode_number
            existing.season_coverage = serialize_release_coverage(coverage)
            existing.score = evaluation.total_score
            existing.passed_rules = evaluation.passed
            existing.rejection_reason = (
                evaluation.rejection_reason[:500] if evaluation.rejection_reason else None
            )
            records_by_key[dedupe_key] = existing
            matched_keys.add(dedupe_key)
        else:
            # INSERT new record
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
                files=release.files,
                uploaded_by=release.uploaded_by,
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
            matched_keys.add(dedupe_key)

    # Delete existing records not matched by any new evaluation
    for key, record in existing_by_key.items():
        if key not in matched_keys:
            await db.delete(record)
    for record in extra_records:
        await db.delete(record)

    await db.commit()
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
    coverage = cached_parse_release_coverage(release.title)

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
            files=release.files,
            uploaded_by=release.uploaded_by,
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
        record.files = release.files
        record.uploaded_by = release.uploaded_by
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
