"""Service for downloading and handling torrent files."""

from pathlib import Path

import httpx


class TorrentService:
    """
    Service for downloading and handling torrent files.
    """

    @staticmethod
    async def download_torrent(url: str, save_path: Path) -> bool:
        """
        Download a torrent file from URL.

        Args:
            url: The URL to download from
            save_path: Where to save the file

        Returns:
            True if successful, False otherwise
        """
        if not url.startswith("http"):
            return False

        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(url, timeout=60.0)
                response.raise_for_status()

                # Check if it's actually a torrent file
                content = response.content
                if not content.startswith(b"d8:"):
                    # Not a valid torrent file
                    return False

                with open(save_path, "wb") as f:
                    f.write(content)
                return True
            except (httpx.RequestError, httpx.HTTPStatusError):
                return False

    @staticmethod
    def validate_torrent_file(path: Path) -> bool:
        """
        Validate that a file is a valid torrent.

        Torrent files start with "d8:" (bencode dictionary)
        """
        try:
            with open(path, "rb") as f:
                header = f.read(10)
                return header.startswith(b"d8:")
        except OSError:
            return False
