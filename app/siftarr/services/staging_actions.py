"""Helpers for staging or sending selected releases."""

import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.siftarr.config import get_settings
from app.siftarr.models.release import Release
from app.siftarr.models.request import MediaType, Request, RequestStatus
from app.siftarr.models.staged_torrent import StagedTorrent
from app.siftarr.services.pending_queue_service import PendingQueueService
from app.siftarr.services.qbittorrent_service import MediaCategory, QbittorrentService
from app.siftarr.services.release_parser import (
    is_exact_single_episode_release,
    parse_release_coverage,
)
from app.siftarr.services.release_storage import build_prowlarr_release
from app.siftarr.services.staging_service import StagingService

logger = logging.getLogger(__name__)


async def _get_active_staged_torrents(
    db: AsyncSession,
    request_id: int,
) -> list[StagedTorrent]:
    """Load currently staged torrents for a request."""
    result = await db.execute(
        select(StagedTorrent)
        .where(
            StagedTorrent.request_id == request_id,
            StagedTorrent.status == "staged",
        )
        .order_by(StagedTorrent.created_at.asc(), StagedTorrent.id.asc())
    )
    return list(result.scalars().all())


def _get_exact_single_episode_scope(title: str) -> tuple[int, int] | None:
    """Return exact episode scope for titles that target one TV episode."""
    coverage = parse_release_coverage(title)
    season_number = coverage.season_number
    episode_number = coverage.episode_number
    if season_number is None or episode_number is None:
        return None
    if not is_exact_single_episode_release(title, season_number, episode_number):
        return None
    return season_number, episode_number


def _filter_active_staged_torrents_for_release(
    request: Request,
    release: Release,
    active_staged: list[StagedTorrent],
) -> list[StagedTorrent]:
    """Scope active staged torrents to the release target when appropriate."""
    if request.media_type != MediaType.TV:
        return active_staged

    release_scope = _get_exact_single_episode_scope(release.title)
    if release_scope is None:
        return active_staged

    return [
        staged
        for staged in active_staged
        if _get_exact_single_episode_scope(staged.title) == release_scope
    ]


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


async def _delete_superseded_staged_torrents(
    db: AsyncSession,
    staging_service: StagingService,
    torrents: list[StagedTorrent],
) -> bool:
    """Delete superseded staged rows and any local staging files."""
    deleted_any = False
    for torrent in torrents:
        await staging_service.delete_staged_files(torrent)
        await db.delete(torrent)
        deleted_any = True
    return deleted_any


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

    runtime_settings = get_settings()
    queue_service = PendingQueueService(db)
    usable_releases = [release for release in releases if release is not None]
    if not usable_releases:
        raise RuntimeError("No stored releases were available to use.")

    if runtime_settings.staging_mode_enabled:
        staging_service = StagingService(db)
        staged_ids: list[int] = []
        replaced_active_selection = False
        deleted_superseded = False

        for release in usable_releases:
            active_staged = await _get_active_staged_torrents(db, request.id)
            relevant_active_staged = _filter_active_staged_torrents_for_release(
                request,
                release,
                active_staged,
            )
            existing = next(
                (stage for stage in relevant_active_staged if stage.title == release.title),
                None,
            )

            if existing is None:
                staged = await staging_service.save_release(
                    build_prowlarr_release(release),
                    request,
                    score=release.score,
                    selection_source=selection_source,
                )
                staged_ids.append(staged.id)
                logger.info(
                    "Release staged: request_id=%s title=%s staged_id=%s score=%s",
                    request.id,
                    release.title,
                    staged.id,
                    release.score,
                )
                preserved_stage_id = staged.id
            else:
                staged_ids.append(existing.id)
                preserved_stage_id = existing.id

            if selection_source == "manual":
                superseded = [
                    current
                    for current in relevant_active_staged
                    if current.id != preserved_stage_id
                ]
                if superseded:
                    deleted_superseded = await _delete_superseded_staged_torrents(
                        db,
                        staging_service,
                        superseded,
                    ) or deleted_superseded
                    replaced_active_selection = True

        if deleted_superseded:
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

        added_hashes.append(torrent_hash)
        logger.info(
            "Torrent sent to qBittorrent: request_id=%s title=%s hash=%s category=%s",
            request.id,
            release.title,
            torrent_hash,
            _get_media_category(request).value,
        )

    await db.commit()
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
