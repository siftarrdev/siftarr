"""Service for interacting with qBittorrent API."""

import qbittorrentapi
from typing import Optional
from enum import Enum


class MediaCategory(str, Enum):
    """Media categories for torrent categorization."""

    MOVIES = "radarr"
    TV = "sonarr"


class QbittorrentService:
    """Service for interacting with qBittorrent API."""

    def __init__(self) -> None:
        """Initialize the qBittorrent service."""
        from arbitratarr.config import get_settings

        self.settings = get_settings()
        self._client: Optional[qbittorrentapi.Client] = None

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
            await self.client.auth.log_in()
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
            categories = await self.client.torrents_categories()
            if category not in categories:
                await self.client.torrents_create_category(
                    name=category,
                    save_path=None,
                )
            return True
        except Exception:
            return False

    async def add_torrent(
        self,
        torrent_path: Optional[str] = None,
        magnet_uri: Optional[str] = None,
        category: MediaCategory = MediaCategory.MOVIES,
        download_path: Optional[str] = None,
        is_paused: bool = False,
        ratio_limit: Optional[float] = None,
        seeding_time_limit: Optional[int] = None,
    ) -> Optional[str]:
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

            # Prepare kwargs
            kwargs: dict[str, object] = {
                "category": category.value,
                "is_paused": is_paused,
            }

            if download_path:
                kwargs["download_path"] = download_path
            if ratio_limit is not None:
                kwargs["ratio_limit"] = ratio_limit
            if seeding_time_limit is not None:
                kwargs["seeding_time_limit"] = seeding_time_limit

            # Add torrent
            if magnet_uri:
                result = await self.client.torrents_add(
                    urls=magnet_uri,
                    **kwargs,
                )
            elif torrent_path:
                with open(torrent_path, "rb") as f:
                    torrent_data = f.read()
                result = await self.client.torrents_add(
                    torrent_files=[torrent_data],
                    **kwargs,
                )
            else:
                raise ValueError("Either torrent_path or magnet_uri must be provided")

            # Check result - qBittorrent returns "Ok." on success
            if result == "Ok.":
                # Get torrent hash if we have a magnet URI
                if magnet_uri:
                    torrents = await self.client.torrents_info()
                    # Find the torrent we just added (most recent)
                    for torrent in sorted(torrents, key=lambda t: t.added_on, reverse=True):
                        if magnet_uri in (torrent.magnet_uri or ""):
                            return torrent.hash
                return result
            return None
        except Exception as e:
            print(f"Error adding torrent: {e}")
            return None

    async def get_torrent_info(self, torrent_hash: str) -> Optional[dict]:
        """Get information about a torrent.

        Args:
            torrent_hash: The torrent hash.

        Returns:
            A dict containing torrent information if found, None otherwise.
        """
        try:
            torrents = await self.client.torrents_info(torrent_hashes=torrent_hash)
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
            torrents = await self.client.torrents_info(category=category)
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
            await self.client.torrents_delete(
                torrent_hashes=torrent_hash,
                delete_files=delete_files,
            )
            return True
        except Exception:
            return False
