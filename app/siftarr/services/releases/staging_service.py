"""Service for managing staged torrents and release handoff.

Consolidates staging, torrent download/validation, and the ``use_releases``
handoff workflow into a single service boundary.
"""

import json
import logging
import os
import re
from datetime import UTC, datetime
from pathlib import Path

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.siftarr.config import get_settings
from app.siftarr.models.episode import Episode
from app.siftarr.models.release import Release
from app.siftarr.models.request import MediaType, Request, RequestStatus
from app.siftarr.models.season import Season
from app.siftarr.models.staged_torrent import StagedTorrent
from app.siftarr.services.integrations.prowlarr_service import ProwlarrRelease
from app.siftarr.services.integrations.qbittorrent_service import MediaCategory, QbittorrentService
from app.siftarr.services.lifecycle.episode_derive import (
    derive_request_status_from_episodes,
    derive_season_status,
)
from app.siftarr.services.lifecycle.pending_queue_service import PendingQueueService
from app.siftarr.services.releases.release_parser import (
    cached_parse_release_coverage,
)
from app.siftarr.services.releases.release_serializers import (
    serialize_target_scope,
    tv_target_scopes_overlap,
)
from app.siftarr.services.releases.release_storage import build_prowlarr_release
from app.siftarr.services.utils.http_client import get_shared_client

STAGING_DIR = Path("/data/staging")

logger = logging.getLogger(__name__)


# ── Torrent download / validation helpers (merged from torrent_service.py) ──


async def download_torrent(url: str, save_path: Path) -> bool:
    """
    Download a torrent file from URL.

    Args:
        url: The URL to download from.
        save_path: Where to save the file.

    Returns:
        True if successful, False otherwise.
    """
    if not url.startswith("http"):
        return False

    client = await get_shared_client()
    try:
        response = await client.get(url, timeout=60.0)
        response.raise_for_status()

        content = response.content
        if not content.startswith(b"d8:"):
            return False

        with open(save_path, "wb") as f:
            f.write(content)
        return True
    except (httpx.RequestError, httpx.HTTPStatusError):
        return False


def validate_torrent_file(path: Path) -> bool:
    """
    Validate that a file is a valid torrent.

    Torrent files start with ``d8:`` (bencode dictionary).
    """
    try:
        with open(path, "rb") as f:
            header = f.read(10)
            return header.startswith(b"d8:")
    except OSError:
        return False


# ── Handoff helpers (moved from staging_actions.py) ──


async def _get_active_staged_torrents(
    db: AsyncSession,
    request_id: int,
) -> list[StagedTorrent]:
    """Load currently active staging torrents for a request."""
    result = await db.execute(
        select(StagedTorrent)
        .where(
            StagedTorrent.request_id == request_id,
            StagedTorrent.status.in_(("staged", "approved")),
        )
        .order_by(StagedTorrent.created_at.asc(), StagedTorrent.id.asc())
    )
    return list(result.scalars().all())


def _target_scope_from_title(title: str) -> dict[str, object]:
    coverage = cached_parse_release_coverage(title)
    return serialize_target_scope(
        media_type=MediaType.TV,
        title=title,
        season_number=coverage.season_number,
        episode_number=coverage.episode_number,
    )


def _filter_active_staged_torrents_for_release(
    request: Request,
    release: Release,
    active_staged: list[StagedTorrent],
) -> list[StagedTorrent]:
    """Scope active staged torrents to the release target when appropriate."""
    if request.media_type != MediaType.TV:
        return active_staged

    release_scope = _target_scope_from_title(release.title)
    return [
        staged
        for staged in active_staged
        if tv_target_scopes_overlap(release_scope, _target_scope_from_title(staged.title))
    ]


def _should_delete_superseded_staged_torrents(
    request: Request,
    selection_source: str,
) -> bool:
    """Return whether this selection must remove other active stages in scope.

    Note: The caller already filters active staged torrents via
    ``_filter_active_staged_torrents_for_release()`` so only overlapping
    (in-scope) torrents reach this function.  This guard just ensures we
    don't delete for auto-staged selections on TV requests.
    """
    if request.media_type == MediaType.MOVIE:
        return True
    return selection_source == "manual"


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
            f"Replaced overlapping staged torrent(s) with {staged_count} release(s).",
        )
    return (
        "manual_staged",
        f"Manually staged {staged_count} release(s) for approval.",
    )


async def _delete_superseded_staged_torrents(
    db: AsyncSession,
    staging_service: "StagingService",
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
    *,
    commit: bool = True,
) -> None:
    """Persist the request status.

    For **movies** this is the authoritative field.
    For **TV** this is a derived cache — prefer setting episode statuses
    and calling :func:`_recompute_tv_statuses` instead.
    """
    if request.media_type == MediaType.MOVIE:
        if request.status == new_status:
            return
        request.status = new_status
        request.updated_at = datetime.now(UTC)
        if commit:
            await db.commit()
    # For TV, request.status is derived from episodes — this is a no-op.
    # Callers should set episode statuses and recompute instead.


def _get_media_category(request: Request) -> MediaCategory:
    """Map a request media type to a qBittorrent category."""
    if request.media_type == MediaType.MOVIE:
        return MediaCategory.MOVIES
    return MediaCategory.TV


# ── StagingService ──


class StagingService:
    """
    Service for managing staged torrents and the release handoff workflow.

    When staging mode is enabled, torrents are saved locally instead of
    being sent directly to qBittorrent.

    File format:
    - {sanitized_title}_{release_group}_{request_id}.torrent
    - {sanitized_title}_{release_group}_{request_id}.json (metadata)
    """

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # ── Torrent helpers (merged from TorrentService) ──

    @staticmethod
    async def download_torrent(url: str, save_path: Path) -> bool:
        """Download a torrent file from URL (delegates to module-level helper)."""
        return await download_torrent(url, save_path)

    @staticmethod
    def validate_torrent_file(path: Path) -> bool:
        """Validate that a file is a valid torrent."""
        return validate_torrent_file(path)

    # ── Filename helpers ──

    def _sanitize_filename(self, title: str) -> str:
        """
        Sanitize a title for use in filenames.

        Removes/replaces characters that are problematic in filenames.
        """
        title = re.sub(r"[<>:\"/\\|?*]", "_", title)
        title = re.sub(r"\s+", "_", title)
        title = re.sub(r"_+", "_", title)
        return title[:100]

    def _generate_filename(
        self,
        title: str,
        release_group: str | None,
        request_id: int,
    ) -> str:
        """Generate a human-readable filename."""
        sanitized = self._sanitize_filename(title)
        if release_group:
            return f"{sanitized}_{release_group}_{request_id}"
        return f"{sanitized}_{request_id}"

    # ── Stage release ──

    async def save_release(
        self,
        release: ProwlarrRelease,
        request: Request,
        score: int = 0,
        selection_source: str = "rule",
        *,
        commit: bool = True,
        download_torrent_file: bool = False,
    ) -> StagedTorrent:
        """
        Save a release to staging.

        Creates a sidecar JSON with metadata. Torrent files are not downloaded
        by default; approval can use the magnet URL or stored download URL.

        Args:
            release: The Prowlarr release to stage.
            request: The associated request.
            score: The evaluation score.
            selection_source: ``"rule"`` or ``"manual"``.

        Returns:
            The created StagedTorrent record.
        """
        logger.info(
            "Saving release to staging: title=%s request_id=%s size=%s indexer=%s",
            release.title,
            request.id,
            release.size,
            release.indexer,
        )

        filename = self._generate_filename(
            title=release.title,
            release_group=release.release_group,
            request_id=request.id,
        )

        torrent_path = STAGING_DIR / f"{filename}.torrent"
        json_path = STAGING_DIR / f"{filename}.json"

        STAGING_DIR.mkdir(parents=True, exist_ok=True)

        if (
            download_torrent_file
            and not release.magnet_url
            and release.download_url.startswith("http")
        ):
            try:
                client = await get_shared_client()
                response = await client.get(release.download_url, timeout=60.0)
                response.raise_for_status()
                with open(torrent_path, "wb") as f:
                    f.write(response.content)
                logger.debug("Downloaded torrent file: %s", torrent_path)
            except Exception:
                logger.warning(
                    "Failed to download torrent file from %s for %s — staging without local file; "
                    "approval will use magnet URL if available",
                    release.download_url,
                    release.title,
                    exc_info=True,
                )
        else:
            logger.debug("Staging without local torrent file: %s", release.title)

        metadata = {
            "request": {
                "id": request.id,
                "external_id": request.external_id,
                "media_type": request.media_type.value,
                "tmdb_id": request.tmdb_id,
                "tvdb_id": request.tvdb_id,
                "title": request.title,
                "year": request.year,
            },
            "release": {
                "title": release.title,
                "size": release.size,
                "indexer": release.indexer,
                "resolution": release.resolution,
                "codec": release.codec,
                "release_group": release.release_group,
                "uploaded_by": release.uploaded_by,
                "seeders": release.seeders,
                "leechers": release.leechers,
                "download_url": release.download_url,
                "magnet_url": release.magnet_url,
                "info_hash": release.info_hash,
            },
            "staged_at": datetime.now(UTC).isoformat(),
            "filename": filename,
        }

        with open(json_path, "w") as f:
            json.dump(metadata, f, indent=2)

        # Compute info hash from magnet URI or .torrent file if not provided by the indexer
        info_hash: str | None = release.info_hash
        if not info_hash:
            if release.magnet_url:
                from app.siftarr.services.integrations.qbittorrent_service import (
                    _parse_magnet_info_hash,
                )

                info_hash = _parse_magnet_info_hash(release.magnet_url)
            if not info_hash and torrent_path and torrent_path.exists():
                from app.siftarr.services.integrations.qbittorrent_service import (
                    _torrent_file_info_hash,
                )

                info_hash = _torrent_file_info_hash(str(torrent_path))

        staged = StagedTorrent(
            request_id=request.id,
            torrent_path=str(torrent_path),
            json_path=str(json_path),
            original_filename=filename,
            title=release.title,
            size=release.size,
            indexer=release.indexer,
            score=score,
            magnet_url=release.magnet_url,
            info_hash=info_hash,
            selection_source=selection_source,
            status="staged",
        )

        self.db.add(staged)
        if commit:
            await self.db.commit()
            await self.db.refresh(staged)
        else:
            await self.db.flush()

        return staged

    # ── Staged torrent queries ──

    async def get_staged_torrent(self, torrent_id: int) -> StagedTorrent | None:
        """Get a staged torrent by ID."""
        result = await self.db.execute(select(StagedTorrent).where(StagedTorrent.id == torrent_id))
        return result.scalar_one_or_none()

    async def get_all_staged(self) -> list[StagedTorrent]:
        """Get all staged torrents."""
        result = await self.db.execute(
            select(StagedTorrent)
            .where(StagedTorrent.status == "staged")
            .order_by(StagedTorrent.created_at.desc())
        )
        return list(result.scalars().all())

    # ── File management ──

    async def delete_staged_files(self, staged: StagedTorrent) -> bool:
        """
        Delete the torrent and JSON files for a staged torrent.

        Does NOT delete the database record.
        """
        try:
            if os.path.exists(staged.torrent_path):
                os.remove(staged.torrent_path)
            if os.path.exists(staged.json_path):
                os.remove(staged.json_path)
            return True
        except OSError:
            return False

    async def scan_staging_directory(self) -> list[dict]:
        """
        Scan the staging directory and sync with database.

        Returns list of any orphaned files found.
        """
        orphaned = []

        if not STAGING_DIR.exists():
            return orphaned

        json_files = list(STAGING_DIR.glob("*.json"))

        for json_file in json_files:
            result = await self.db.execute(
                select(StagedTorrent).where(StagedTorrent.json_path == str(json_file))
            )
            staged = result.scalar_one_or_none()

            if not staged:
                orphaned.append(
                    {
                        "json_path": str(json_file),
                        "torrent_path": str(json_file.with_suffix(".torrent")),
                    }
                )

        return orphaned

    @staticmethod
    def is_staging_enabled(db: AsyncSession) -> bool:
        """Check if staging mode is enabled."""
        return False

    # ── Episode-centric TV helpers ─────────────────────────────────

    async def _set_episode_status(
        self,
        *,
        request_id: int,
        season_number: int,
        episode_number: int,
        status: RequestStatus,
    ) -> None:
        """Set a single episode's status (episode-centric ground truth for TV)."""
        result = await self.db.execute(
            select(Episode)
            .join(Season, Episode.season_id == Season.id)
            .where(
                Season.request_id == request_id,
                Season.season_number == season_number,
                Episode.episode_number == episode_number,
            )
        )
        episode = result.scalar_one_or_none()
        if episode:
            episode.status = status

    async def _recompute_tv_statuses(self, request_id: int) -> None:
        """Recompute Season.status and Request.status from episode ground truth.

        Call after any episode mutation for TV requests so the cached summary
        columns stay in sync.
        """
        result = await self.db.execute(
            select(Request)
            .where(Request.id == request_id)
            .options(selectinload(Request.seasons).selectinload(Season.episodes))
        )
        request = result.scalar_one_or_none()
        if not request or request.media_type != MediaType.TV:
            return

        all_episodes: list[Episode] = []
        for season in request.seasons:
            season_episodes = list(season.episodes)
            season.status = derive_season_status(season_episodes)
            all_episodes.extend(season_episodes)

        request.status = derive_request_status_from_episodes(all_episodes)
        request.updated_at = datetime.now(UTC)

    async def _apply_release_to_episodes(
        self,
        release: Release,
        status: RequestStatus,
    ) -> None:
        """Set episode statuses based on a release's coverage.

        Parses the release title to determine which episodes it covers
        (single episode, season pack, multi-season pack, or complete series)
        and sets each covered episode's status.

        This is the episode-centric path — episode status is ground truth.
        """
        request_id = release.request_id
        coverage = cached_parse_release_coverage(release.title)
        if coverage.episode_number is not None and coverage.season_number is not None:
            # Single episode
            await self._set_episode_status(
                request_id=request_id,
                season_number=coverage.season_number,
                episode_number=coverage.episode_number,
                status=status,
            )
        elif coverage.is_complete_series:
            # Complete series — set all episodes for all seasons
            seasons_result = await self.db.execute(
                select(Season).where(Season.request_id == request_id)
            )
            seasons = list(seasons_result.scalars().all())
            for season in seasons:
                episodes_result = await self.db.execute(
                    select(Episode).where(Episode.season_id == season.id)
                )
                for ep in episodes_result.scalars().all():
                    ep.status = status
        elif coverage.season_numbers:
            # Season pack or multi-season pack
            for season_num in coverage.season_numbers:
                episodes_result = await self.db.execute(
                    select(Episode)
                    .join(Season, Episode.season_id == Season.id)
                    .where(
                        Season.request_id == request_id,
                        Season.season_number == season_num,
                    )
                )
                for ep in episodes_result.scalars().all():
                    ep.status = status

    # ── Release handoff (use_releases) ──

    async def use_releases(
        self,
        request: Request,
        releases: list[Release],
        *,
        selection_source: str = "manual",
    ) -> dict[str, object]:
        """Stage or send one or more stored releases for a request.

        Args:
            request: The request to associate releases with.
            releases: Stored Release records to stage or send.
            selection_source: ``"rule"`` or ``"manual"``.

        Returns:
            Dict with status, action, message, and relevant IDs.
        """
        logger.info(
            "use_releases called: request_id=%s release_count=%s selection_source=%s",
            request.id,
            len(releases),
            selection_source,
        )

        runtime_settings = get_settings()
        queue_service = PendingQueueService(self.db)
        usable_releases = [release for release in releases if release is not None]
        if not usable_releases:
            raise RuntimeError("No stored releases were available to use.")

        if runtime_settings.staging_mode_enabled:
            staged_ids: list[int] = []
            replaced_active_selection = False
            deleted_superseded = False

            logger.info(
                "Staging releases: request_id=%s release_count=%s selection_source=%s",
                request.id,
                len(usable_releases),
                selection_source,
            )

            for release in usable_releases:
                active_staged = await _get_active_staged_torrents(self.db, request.id)
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
                    staged = await self.save_release(
                        build_prowlarr_release(release),
                        request,
                        score=release.score,
                        selection_source=selection_source,
                        commit=False,
                    )
                    staged_ids.append(staged.id)
                    logger.debug(
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

                if _should_delete_superseded_staged_torrents(request, selection_source):
                    superseded = [
                        current
                        for current in relevant_active_staged
                        if current.id != preserved_stage_id
                    ]
                    if superseded:
                        deleted_superseded = (
                            await _delete_superseded_staged_torrents(
                                self.db,
                                self,
                                superseded,
                            )
                            or deleted_superseded
                        )
                        replaced_active_selection = True

            if request.media_type == MediaType.TV:
                # Episode-centric: set covered episode statuses, then recompute
                for release in usable_releases:
                    await self._apply_release_to_episodes(release, RequestStatus.STAGED)
                await self._recompute_tv_statuses(request.id)
            else:
                await _set_request_status(self.db, request, RequestStatus.STAGED, commit=False)
            await queue_service.remove_from_queue(request.id)
            await self.db.commit()
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

        await self.db.commit()
        if request.media_type == MediaType.TV:
            # Episode-centric: set covered episode statuses, then recompute
            for release in usable_releases:
                await self._apply_release_to_episodes(release, RequestStatus.DOWNLOADING)
                await self._recompute_tv_statuses(request.id)
        else:
            await _set_request_status(self.db, request, RequestStatus.DOWNLOADING)
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
