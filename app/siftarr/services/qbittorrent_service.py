"""Service for interacting with qBittorrent API."""

import asyncio
import logging
from enum import StrEnum

import qbittorrentapi

from app.siftarr.config import Settings, get_settings

logger = logging.getLogger(__name__)


class MediaCategory(StrEnum):
    """Media categories for torrent categorization."""

    MOVIES = "radarr"
    TV = "sonarr"


class QbittorrentService:
    """Service for interacting with qBittorrent API."""

    def __init__(self, settings: Settings | None = None) -> None:
        """Initialize the qBittorrent service."""
        self.settings = settings or get_settings()
        self._client: qbittorrentapi.Client | None = None

    @property
    def client(self) -> qbittorrentapi.Client:
        """Get or create qBittorrent client."""
        if self._client is None:
            self._client = qbittorrentapi.Client(
                host=str(self.settings.qbittorrent_url),
                username=self.settings.qbittorrent_username,
                password=self.settings.qbittorrent_password,
            )
        return self._client

    async def authenticate(self) -> bool:
        """Authenticate with qBittorrent.

        Returns:
            True if authentication successful, False otherwise.
        """
        try:
            await asyncio.to_thread(self.client.auth.log_in)
            return True
        except qbittorrentapi.LoginFailed:
            return False

    async def ensure_category_exists(self, category: str) -> bool:
        """Ensure a category exists in qBittorrent, create if needed.

        Args:
            category: The category name to ensure exists.

        Returns:
            True if category exists or was created, False otherwise.
        """
        try:
            categories = await asyncio.to_thread(self.client.torrents_categories)
            if category not in categories:
                await asyncio.to_thread(
                    self.client.torrents_create_category,
                    name=category,
                    save_path=None,
                )
            return True
        except Exception:
            return False

    async def add_torrent(
        self,
        torrent_path: str | None = None,
        magnet_uri: str | None = None,
        category: MediaCategory = MediaCategory.MOVIES,
        download_path: str | None = None,
        is_paused: bool = False,
        ratio_limit: float | None = None,
        seeding_time_limit: int | None = None,
    ) -> str | None:
        """Add a torrent to qBittorrent.

        Args:
            torrent_path: Path to .torrent file (mutually exclusive with magnet_uri).
            magnet_uri: Magnet URI (mutually exclusive with torrent_path).
            category: Category to assign (radarr for movies, sonarr for TV).
            download_path: Optional custom download path.
            is_paused: Start paused.
            ratio_limit: Seed ratio limit.
            seeding_time_limit: Seeding time limit in minutes.

        Returns:
            Torrent hash if successful, None otherwise.
        """
        try:
            # Ensure category exists
            await self.ensure_category_exists(category.value)

            # Add torrent
            if magnet_uri:
                result = await asyncio.to_thread(
                    self.client.torrents_add,
                    urls=magnet_uri,
                    category=category.value,
                    is_paused=is_paused,
                    download_path=download_path,
                    ratio_limit=ratio_limit,
                    seeding_time_limit=seeding_time_limit,
                )
            elif torrent_path:
                with open(torrent_path, "rb") as f:
                    torrent_data = f.read()
                result = await asyncio.to_thread(
                    self.client.torrents_add,
                    torrent_files=[torrent_data],
                    category=category.value,
                    is_paused=is_paused,
                    download_path=download_path,
                    ratio_limit=ratio_limit,
                    seeding_time_limit=seeding_time_limit,
                )
            else:
                raise ValueError("Either torrent_path or magnet_uri must be provided")

            # Check result - qBittorrent returns "Ok." on success
            if result == "Ok.":
                # Get torrent hash if we have a magnet URI
                if magnet_uri:
                    torrents = await asyncio.to_thread(self.client.torrents_info)
                    # Find the torrent we just added (most recent)
                    for torrent in sorted(torrents, key=lambda t: t.added_on, reverse=True):
                        if magnet_uri in (torrent.magnet_uri or ""):
                            return str(torrent.hash)
                return str(result) if result == "Ok." else None
            return None
        except Exception as e:
            logger.error("Error adding torrent: %s", e)
            return None

    async def get_torrent_info(self, torrent_hash: str) -> dict | None:
        """Get information about a torrent.

        Args:
            torrent_hash: The torrent hash.

        Returns:
            A dict containing torrent information if found, None otherwise.
        """
        try:
            torrents = await asyncio.to_thread(
                self.client.torrents_info,
                torrent_hashes=torrent_hash,
            )
            if torrents:
                torrent = torrents[0]
                return {
                    "hash": torrent.hash,
                    "name": torrent.name,
                    "size": torrent.size,
                    "progress": torrent.progress,
                    "state": torrent.state,
                    "category": torrent.category,
                    "ratio": torrent.ratio,
                    "added_on": torrent.added_on,
                    "completed_on": torrent.completed_on,
                    "download_location": torrent.download_location,
                }
            return None
        except Exception:
            return None

    async def get_torrents_by_category(self, category: str) -> list[dict]:
        """Get all torrents in a category.

        Args:
            category: The category name to filter by.

        Returns:
            A list of dicts containing torrent information.
        """
        try:
            torrents = await asyncio.to_thread(
                self.client.torrents_info,
                category=category,
            )
            return [
                {
                    "hash": t.hash,
                    "name": t.name,
                    "size": t.size,
                    "progress": t.progress,
                    "state": t.state,
                }
                for t in torrents
            ]
        except Exception:
            return []

    async def delete_torrent(self, torrent_hash: str, delete_files: bool = False) -> bool:
        """Delete a torrent from qBittorrent.

        Args:
            torrent_hash: The torrent hash to delete.
            delete_files: Whether to delete downloaded files.

        Returns:
            True if deletion successful, False otherwise.
        """
        try:
            await asyncio.to_thread(
                self.client.torrents_delete,
                torrent_hashes=torrent_hash,
                delete_files=delete_files,
            )
            return True
        except Exception:
            return False
