"""Helpers for persisting and using searched releases."""

from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.arbitratarr.models.release import Release
from app.arbitratarr.models.request import MediaType, Request, RequestStatus
from app.arbitratarr.models.staged_torrent import StagedTorrent
from app.arbitratarr.services.pending_queue_service import PendingQueueService
from app.arbitratarr.services.prowlarr_service import ProwlarrRelease
from app.arbitratarr.services.qbittorrent_service import MediaCategory, QbittorrentService
from app.arbitratarr.services.rule_engine import ReleaseEvaluation
from app.arbitratarr.services.runtime_settings import get_effective_settings
from app.arbitratarr.services.staging_service import StagingService


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
    await db.execute(delete(Release).where(Release.request_id == request_id))

    records_by_title: dict[str, Release] = {}
    seen_keys: set[str] = set()
    for evaluation in evaluations:
        release = evaluation.release
        dedupe_key = release.info_hash or release.title
        if dedupe_key in seen_keys:
            continue
        seen_keys.add(dedupe_key)

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
            score=evaluation.total_score,
            passed_rules=evaluation.passed,
        )
        db.add(record)
        records_by_title[record.title] = record

    await db.commit()
    for record in records_by_title.values():
        await db.refresh(record)
    return records_by_title


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
    runtime_settings = await get_effective_settings(db)
    queue_service = PendingQueueService(db)
    usable_releases = [release for release in releases if release is not None]
    if not usable_releases:
        raise RuntimeError("No stored releases were available to use.")

    if runtime_settings.staging_mode_enabled:
        staging_service = StagingService(db)
        staged_ids: list[int] = []
        for release in usable_releases:
            existing_result = await db.execute(
                select(StagedTorrent).where(
                    StagedTorrent.request_id == request.id,
                    StagedTorrent.title == release.title,
                    StagedTorrent.status.in_(["staged", "approved"]),
                )
            )
            existing = existing_result.scalar_one_or_none()
            if existing is not None:
                staged_ids.append(existing.id)
                continue

            staged = await staging_service.save_release(
                build_prowlarr_release(release),
                request,
                score=release.score,
                selection_source=selection_source,
            )
            staged_ids.append(staged.id)

        await _set_request_status(db, request, RequestStatus.STAGED)
        await queue_service.remove_from_queue(request.id)
        return {
            "status": "staged",
            "message": f"Staged {len(staged_ids)} release(s) for approval.",
            "staged_ids": staged_ids,
        }

    qbittorrent = QbittorrentService(settings=runtime_settings)
    added_hashes: list[str] = []
    for release in usable_releases:
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

    await db.commit()
    await _set_request_status(db, request, RequestStatus.DOWNLOADING)
    await queue_service.remove_from_queue(request.id)
    return {
        "status": "downloading",
        "message": f"Sent {len(added_hashes)} release(s) to qBittorrent.",
        "torrent_hashes": added_hashes,
    }
