"""Helpers for persisting and using searched releases."""

import logging
from datetime import UTC, datetime

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.siftarr.models.episode import Episode
from app.siftarr.models.release import Release
from app.siftarr.models.request import MediaType, Request, RequestStatus
from app.siftarr.models.staged_torrent import StagedTorrent
from app.siftarr.services.pending_queue_service import PendingQueueService
from app.siftarr.services.prowlarr_service import ProwlarrRelease
from app.siftarr.services.qbittorrent_service import MediaCategory, QbittorrentService
from app.siftarr.services.release_parser import (
    parse_release_coverage,
    parse_season_episode,
    serialize_release_coverage,
)
from app.siftarr.services.rule_engine import ReleaseEvaluation
from app.siftarr.services.runtime_settings import get_effective_settings
from app.siftarr.services.staging_service import StagingService

logger = logging.getLogger(__name__)


async def _get_active_staged_torrents(
    db: AsyncSession,
    request_id: int,
) -> list[StagedTorrent]:
    """Load currently active staged/approved torrents for a request."""
    result = await db.execute(
        select(StagedTorrent)
        .where(
            StagedTorrent.request_id == request_id,
            StagedTorrent.status.in_(["staged", "approved"]),
        )
        .order_by(StagedTorrent.created_at.asc(), StagedTorrent.id.asc())
    )
    return list(result.scalars().all())


def _replacement_reason_for_selection(
    current: StagedTorrent,
    *,
    selection_source: str,
) -> str:
    """Return a consistent audit reason for active selection replacement."""
    if current.status == "approved":
        return "Manually replaced approved selection from request details"
    if selection_source == "manual":
        return "Manually replaced staged selection from request details"
    return "Replaced staged selection"


def _retire_replaced_selection(
    current: StagedTorrent,
    replacement: StagedTorrent,
    *,
    selection_source: str,
) -> None:
    """Mark an active staged/approved torrent as replaced by another selection."""
    current.status = "replaced"
    current.replaced_by_id = replacement.id
    current.replaced_at = datetime.now(UTC)
    current.replacement_reason = _replacement_reason_for_selection(
        current,
        selection_source=selection_source,
    )


def _staged_selection_outcome(
    *,
    selection_source: str,
    staged_count: int,
    replaced_active_selection: bool,
) -> tuple[str, str]:
    """Return a clear operator-facing action/message pair for staging mode."""
    if selection_source == "rule":
        return (
            "auto_staged",
            f"Auto-staged {staged_count} release(s) for approval.",
        )
    if replaced_active_selection:
        return (
            "replaced_active_selection",
            f"Replaced the active staged selection with {staged_count} release(s).",
        )
    return (
        "manual_staged",
        f"Manually staged {staged_count} release(s) for approval.",
    )


async def _purge_releases(
    db: AsyncSession,
    *,
    request_id: int | None = None,
    commit: bool = True,
) -> dict[str, int]:
    """Detach episode links and delete stored releases, optionally for one request."""
    release_ids_query = select(Release.id)
    release_delete_query = delete(Release)
    if request_id is not None:
        release_ids_query = release_ids_query.where(Release.request_id == request_id)
        release_delete_query = release_delete_query.where(Release.request_id == request_id)

    detached_episode_count = (
        await db.scalar(
            select(func.count())
            .select_from(Episode)
            .where(Episode.release_id.in_(release_ids_query))
        )
        or 0
    )

    if detached_episode_count:
        await db.execute(
            update(Episode).where(Episode.release_id.in_(release_ids_query)).values(release_id=None)
        )

    deleted_release_count = detached_episode_count
    await db.execute(release_delete_query)
    if commit:
        await db.commit()

    return {
        "deleted_releases": deleted_release_count,
        "detached_episode_refs": detached_episode_count,
    }


async def clear_release_search_cache(db: AsyncSession) -> dict[str, int]:
    """Clear persisted search results and detach stale episode release links."""
    result = await _purge_releases(db)

    logger.info(
        "Cleared persisted release search cache: deleted_releases=%s detached_episode_refs=%s",
        result["deleted_releases"],
        result["detached_episode_refs"],
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

    records_by_title: dict[str, Release] = {}
    seen_keys: set[str] = set()
    for evaluation in evaluations:
        release = evaluation.release
        dedupe_key = release.info_hash or release.title
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
        )
        db.add(record)
        records_by_title[record.title] = record

    await db.commit()
    for record in records_by_title.values():
        await db.refresh(record)
    return records_by_title


async def persist_manual_release(
    db: AsyncSession,
    request: Request,
    release: ProwlarrRelease,
    evaluation: ReleaseEvaluation,
) -> Release:
    """Persist or update a manually selected release so existing use logic can reuse it."""
    if not (release.magnet_url or release.download_url):
        raise RuntimeError(f"Release '{release.title}' has no usable download source.")

    parsed = parse_season_episode(release.title)
    coverage = parse_release_coverage(release.title)

    filters = [Release.request_id == request.id, Release.title == release.title]
    if release.info_hash:
        filters = [
            Release.request_id == request.id,
            or_(Release.info_hash == release.info_hash, Release.title == release.title),
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

    await db.commit()
    await db.refresh(record)
    return record


async def _set_request_status(
    db: AsyncSession,
    request: Request,
    new_status: RequestStatus,
) -> None:
    """Persist the request status, even if an older bad state needs correcting."""
    if request.status == new_status:
        return

    request.status = new_status
    request.updated_at = datetime.now(UTC)
    await db.commit()


def _get_media_category(request: Request) -> MediaCategory:
    """Map a request media type to a qBittorrent category."""
    if request.media_type == MediaType.MOVIE:
        return MediaCategory.MOVIES
    return MediaCategory.TV


async def use_releases(
    db: AsyncSession,
    request: Request,
    releases: list[Release],
    *,
    selection_source: str = "manual",
) -> dict[str, object]:
    """Stage or send one or more stored releases for a request."""
    logger.info(
        "use_releases called: request_id=%s release_count=%s selection_source=%s",
        request.id,
        len(releases),
        selection_source,
    )

    runtime_settings = await get_effective_settings(db)
    queue_service = PendingQueueService(db)
    usable_releases = [release for release in releases if release is not None]
    if not usable_releases:
        raise RuntimeError("No stored releases were available to use.")

    if runtime_settings.staging_mode_enabled:
        staging_service = StagingService(db)
        staged_ids: list[int] = []
        replaced_active_selection = False
        for release in usable_releases:
            active_staged = await _get_active_staged_torrents(db, request.id)
            existing = next(
                (stage for stage in active_staged if stage.title == release.title), None
            )
            if existing is not None:
                if selection_source == "manual":
                    for current in active_staged:
                        if current.id == existing.id:
                            continue
                        _retire_replaced_selection(
                            current,
                            existing,
                            selection_source=selection_source,
                        )
                        replaced_active_selection = True
                staged_ids.append(existing.id)
                continue

            staged = await staging_service.save_release(
                build_prowlarr_release(release),
                request,
                score=release.score,
                selection_source=selection_source,
            )

            if selection_source == "manual":
                for current in active_staged:
                    _retire_replaced_selection(
                        current,
                        staged,
                        selection_source=selection_source,
                    )
                    replaced_active_selection = True

            staged_ids.append(staged.id)
            logger.info(
                "Release staged: request_id=%s title=%s staged_id=%s score=%s",
                request.id,
                release.title,
                staged.id,
                release.score,
            )

        if replaced_active_selection:
            await db.commit()

        await _set_request_status(db, request, RequestStatus.STAGED)
        await queue_service.remove_from_queue(request.id)
        action, message = _staged_selection_outcome(
            selection_source=selection_source,
            staged_count=len(staged_ids),
            replaced_active_selection=replaced_active_selection,
        )
        logger.info(
            "Request staged: request_id=%s staged_count=%s action=%s selection_source=%s",
            request.id,
            len(staged_ids),
            action,
            selection_source,
        )
        return {
            "status": "staged",
            "action": action,
            "message": message,
            "staged_ids": staged_ids,
        }

    qbittorrent = QbittorrentService(settings=runtime_settings)
    added_hashes: list[str] = []
    already_sent_titles: list[str] = []
    for release in usable_releases:
        if release.is_downloaded:
            already_sent_titles.append(release.title)
            continue

        source = release.magnet_url or release.download_url
        if not source:
            raise RuntimeError(f"Release '{release.title}' has no usable download source.")

        torrent_hash = await qbittorrent.add_torrent(
            magnet_uri=source,
            category=_get_media_category(request),
        )
        if torrent_hash is None:
            raise RuntimeError(f"Failed to send '{release.title}' to qBittorrent.")

        release.is_downloaded = True
        release.downloaded_at = datetime.now(UTC)
        added_hashes.append(torrent_hash)
        logger.info(
            "Torrent sent to qBittorrent: request_id=%s title=%s hash=%s category=%s",
            request.id,
            release.title,
            torrent_hash,
            _get_media_category(request).value,
        )

    await db.commit()
    if not added_hashes and already_sent_titles:
        await _set_request_status(db, request, RequestStatus.DOWNLOADING)
        await queue_service.remove_from_queue(request.id)
        return {
            "status": "downloading",
            "message": f"Release already sent: {', '.join(already_sent_titles)}.",
            "torrent_hashes": [],
            "already_sent_titles": already_sent_titles,
        }

    await _set_request_status(db, request, RequestStatus.DOWNLOADING)
    await queue_service.remove_from_queue(request.id)
    logger.info(
        "Request downloading: request_id=%s torrent_count=%s",
        request.id,
        len(added_hashes),
    )
    return {
        "status": "downloading",
        "message": f"Sent {len(added_hashes)} release(s) to qBittorrent.",
        "torrent_hashes": added_hashes,
    }
