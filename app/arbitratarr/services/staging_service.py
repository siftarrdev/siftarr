"""Service for managing staged torrents."""

import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.arbitratarr.models.request import Request
from app.arbitratarr.models.staged_torrent import StagedTorrent
from app.arbitratarr.services.prowlarr_service import ProwlarrRelease

STAGING_DIR = Path("/data/staging")


class StagingService:
    """
    Service for managing staged torrents.

    When staging mode is enabled, torrents are saved locally instead of
    being sent directly to qBittorrent.

    File format:
    - {sanitized_title}_{release_group}_{request_id}.torrent
    - {sanitized_title}_{release_group}_{request_id}.json (metadata)
    """

    def __init__(self, db: AsyncSession | None) -> None:
        self.db = db

    def _sanitize_filename(self, title: str) -> str:
        """
        Sanitize a title for use in filenames.

        Removes/replaces characters that are problematic in filenames.
        """
        # Replace problematic characters
        title = re.sub(r"[<>:\"/\\|?*]", "_", title)
        # Replace multiple spaces/underscores with single underscore
        title = re.sub(r"\s+", "_", title)
        title = re.sub(r"_+", "_", title)
        # Truncate to reasonable length
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

    async def save_release(
        self,
        release: ProwlarrRelease,
        request: Request,
        score: int = 0,
        selection_source: str = "rule",
    ) -> StagedTorrent:
        """
        Save a release to staging.

        Downloads the torrent file and creates a sidecar JSON with metadata.

        Args:
            release: The Prowlarr release to stage
            request: The associated request

        Returns:
            The created StagedTorrent record

        Raises:
            RuntimeError: If database session is not available
        """
        if self.db is None:
            raise RuntimeError("Database session is required for save_release")
        # Generate filename
        filename = self._generate_filename(
            title=release.title,
            release_group=release.release_group,
            request_id=request.id,
        )

        torrent_path = STAGING_DIR / f"{filename}.torrent"
        json_path = STAGING_DIR / f"{filename}.json"

        # Ensure staging directory exists
        STAGING_DIR.mkdir(parents=True, exist_ok=True)

        # Download torrent file if it's a URL
        if release.download_url.startswith("http"):
            async with httpx.AsyncClient() as client:
                response = await client.get(release.download_url, timeout=60.0)
                response.raise_for_status()
                with open(torrent_path, "wb") as f:
                    f.write(response.content)
        else:
            # It's already a magnet URI or something else
            # We'll store the magnet in the JSON
            pass

        # Create metadata JSON
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
                "seeders": release.seeders,
                "leechers": release.leechers,
                "download_url": release.download_url,
                "magnet_url": release.magnet_url,
            },
            "staged_at": datetime.now(UTC).isoformat(),
            "filename": filename,
        }

        with open(json_path, "w") as f:
            json.dump(metadata, f, indent=2)

        # Create database record
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
            selection_source=selection_source,
            status="staged",
        )

        self.db.add(staged)
        await self.db.commit()
        await self.db.refresh(staged)

        return staged

    async def get_staged_torrent(self, torrent_id: int) -> StagedTorrent | None:
        """Get a staged torrent by ID."""
        if self.db is None:
            raise RuntimeError("Database session is required for get_staged_torrent")
        result = await self.db.execute(select(StagedTorrent).where(StagedTorrent.id == torrent_id))
        return result.scalar_one_or_none()

    async def get_all_staged(self) -> list[StagedTorrent]:
        """Get all staged torrents."""
        if self.db is None:
            raise RuntimeError("Database session is required for get_all_staged")
        result = await self.db.execute(
            select(StagedTorrent)
            .where(StagedTorrent.status == "staged")
            .order_by(StagedTorrent.created_at.desc())
        )
        return list(result.scalars().all())

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
        if self.db is None:
            raise RuntimeError("Database session is required for scan_staging_directory")

        orphaned = []

        if not STAGING_DIR.exists():
            return orphaned

        # Get all JSON files
        json_files = list(STAGING_DIR.glob("*.json"))

        for json_file in json_files:
            # Check if we have a DB record
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
        # This would check the database setting
        # For now, return False as default
        return False
