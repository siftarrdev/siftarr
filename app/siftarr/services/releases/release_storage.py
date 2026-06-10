"""Helpers for persisting searched releases."""

import logging
from typing import Any, cast

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.siftarr.models.release import Release
from app.siftarr.models.request import Request
from app.siftarr.services.decisions.rule_engine import ReleaseEvaluation
from app.siftarr.services.integrations.prowlarr_service import ProwlarrRelease
from app.siftarr.services.releases.release_parser import (
    cached_parse_release_coverage,
    parse_season_episode,
    parse_stored_release_coverage,
    serialize_release_coverage,
)
from app.siftarr.services.releases.release_serializers import compact_rule_evidence

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
    *,
    scope: dict[str, object] | None = None,
    source: str = "automatic",
) -> dict[str, Release]:
    """Upsert stored search results and purge stale rows within the same scope."""
    # 1. Load existing releases for this request
    existing_result = await db.execute(select(Release).where(Release.request_id == request_id))
    existing_records = [
        record
        for record in existing_result.scalars().all()
        if _release_matches_source(record, source)
        and _release_matches_persistence_scope(record, scope)
    ]
    logger.info(
        "Stored release cache loaded: request_id=%s scope=%s source=db search_source=%s count=%s",
        request_id,
        scope.get("type") if scope else "all",
        source,
        len(existing_records),
    )

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
        rule_evidence = compact_rule_evidence(evaluation)
        parse_metadata = {
            "season_number": parsed.season_number,
            "episode_number": parsed.episode_number,
            "season_numbers": list(coverage.season_numbers),
            "is_complete_series": coverage.is_complete_series,
        }

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
            existing.rule_evidence = rule_evidence
            cast(Any, existing).release_parse_metadata = parse_metadata
            existing.search_source = source
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
                rule_evidence=rule_evidence,
                release_parse_metadata=parse_metadata,
                search_source=source,
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

    deleted_count = sum(1 for key in existing_by_key if key not in matched_keys) + len(
        extra_records
    )
    await db.commit()
    logger.info(
        "Stored search results saved: request_id=%s scope=%s search_source=%s evaluated=%s stored=%s deleted=%s source=db",
        request_id,
        scope.get("type") if scope else "all",
        source,
        len(evaluations),
        len(records_by_key),
        deleted_count,
    )
    return records_by_key


def _release_matches_source(release: Release, source: str) -> bool:
    return (release.search_source or "automatic") == source


def _release_matches_persistence_scope(release: Release, scope: dict[str, object] | None) -> bool:
    """Return whether a stored release belongs to a scoped TV search result set."""
    if scope is None:
        return True

    scope_type = scope.get("type")
    coverage = parse_stored_release_coverage(
        release.season_coverage,
        release.season_number,
        release.episode_number,
    )
    if scope_type == "single_episode":
        return coverage.season_number == scope.get(
            "season_number"
        ) and coverage.episode_number == scope.get("episode_number")
    if scope_type == "season_packs":
        return (
            coverage.episode_number is None
            and not coverage.is_complete_series
            and coverage.season_numbers == (scope.get("season_number"),)
        )
    if scope_type == "multi_season_packs":
        return coverage.episode_number is None and (
            coverage.is_complete_series or len(coverage.season_numbers) > 1
        )
    if scope_type == "season_sweep":
        season_number = scope.get("season_number")
        if not isinstance(season_number, int):
            return True
        return (
            coverage.is_complete_series
            or coverage.season_number == season_number
            or season_number in coverage.season_numbers
        )
    return True


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
            search_source="manual",
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
        record.search_source = "manual"

    await db.commit()
    await db.refresh(record)
    return record
