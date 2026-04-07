import re
from datetime import datetime

import httpx
from pydantic import BaseModel

from app.arbitratarr.config import Settings, get_settings


class ProwlarrRelease(BaseModel):
    """Represents a release from Prowlarr."""

    title: str
    size: int  # bytes
    seeders: int
    leechers: int
    download_url: str
    magnet_url: str | None = None
    info_hash: str | None = None
    indexer: str
    publish_date: datetime | None = None
    resolution: str | None = None
    codec: str | None = None
    release_group: str | None = None


class ProwlarrSearchResult(BaseModel):
    """Result from Prowlarr search."""

    releases: list[ProwlarrRelease]
    query_time_ms: int


class ProwlarrService:
    """Service for interacting with Prowlarr API."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.base_url = str(self.settings.prowlarr_url).rstrip("/")
        self.api_key = self.settings.prowlarr_api_key

    def _get_headers(self) -> dict[str, str]:
        api_key = self.api_key
        if api_key is None:
            api_key = ""
        return {"X-Api-Key": api_key}

    def _parse_release_info(self, release: dict) -> ProwlarrRelease:
        """Parse a release from Prowlarr response."""
        # Parse title for resolution, codec, release group
        title = release.get("title", "")
        resolution = self._extract_resolution(title)
        codec = self._extract_codec(title)
        release_group = self._extract_release_group(title)

        return ProwlarrRelease(
            title=title,
            size=release.get("size", 0),
            seeders=release.get("seeders", 0),
            leechers=release.get("leechers", 0),
            download_url=release.get("downloadUrl", ""),
            magnet_url=release.get("magnetUrl"),
            info_hash=release.get("infoHash"),
            indexer=release.get("indexer", "unknown"),
            publish_date=self._parse_date(release.get("publishDate")),
            resolution=resolution,
            codec=codec,
            release_group=release_group,
        )

    def _extract_resolution(self, title: str) -> str | None:
        """Extract resolution from release title."""
        patterns = [
            (r"2160[pP]|4[kK]", "2160p"),
            (r"1080[pP]", "1080p"),
            (r"720[pP]", "720p"),
            (r"480[pP]", "480p"),
        ]
        for pattern, resolution in patterns:
            if re.search(pattern, title, re.IGNORECASE):
                return resolution
        return None

    def _extract_codec(self, title: str) -> str | None:
        """Extract codec from release title."""
        patterns = [
            (r"x265|H\.?265|HEVC", "x265"),
            (r"x264|H\.?264|AVC", "x264"),
            (r"VP9", "VP9"),
            (r"VP10|AV1", "AV1"),
        ]
        for pattern, codec in patterns:
            if re.search(pattern, title, re.IGNORECASE):
                return codec
        return None

    def _extract_release_group(self, title: str) -> str | None:
        """Extract release group from title."""
        # Common pattern: Title-Y ReleaseGroup or Title.RELEASEGROUP
        patterns = [
            r"-(?P<group>[A-Za-z0-9]+)$",
            r"\.(?P<group>[A-Za-z0-9]+)$",
        ]
        for pattern in patterns:
            match = re.search(pattern, title)
            if match:
                return match.group("group")
        return None

    def _parse_date(self, date_str: str | None) -> datetime | None:
        """Parse date string to datetime."""
        if not date_str:
            return None
        try:
            return datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            return None

    async def search_by_tmdbid(
        self,
        tmdbid: int,
        categories: list[int] | None = None,
    ) -> ProwlarrSearchResult:
        """
        Search for movie releases by TMDB ID.

        Args:
            tmdbid: The TMDB ID to search for
            categories: Optional list of category IDs (default: [2000] for movies)

        Returns:
            ProwlarrSearchResult with list of releases
        """
        import time

        start_time = time.time()

        if categories is None:
            categories = [2000]  # Movies

        endpoint = f"{self.base_url}/api/v1/search"
        headers = self._get_headers()

        params = {
            "type": "movie",
            "tmdbid": f"tmdb:{tmdbid}",
        }

        releases = []
        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(
                    endpoint,
                    headers=headers,
                    params=params,
                    timeout=60.0,
                )
                if response.status_code == 200:
                    results = response.json()
                    for result in results:
                        for release_data in result.get("releases", []):
                            releases.append(self._parse_release_info(release_data))
            except httpx.RequestError:
                pass

        return ProwlarrSearchResult(
            releases=releases,
            query_time_ms=int((time.time() - start_time) * 1000),
        )

    async def search_by_tvdbid(
        self,
        tvdbid: int,
        season: int | None = None,
        episode: int | None = None,
        categories: list[int] | None = None,
    ) -> ProwlarrSearchResult:
        """
        Search for TV releases by TVDB ID.

        Args:
            tvdbid: The TVDB ID to search for
            season: Optional season number (for season pack)
            episode: Optional episode number (for single episode)
            categories: Optional list of category IDs (default: [5000] for TV)

        Returns:
            ProwlarrSearchResult with list of releases
        """
        import time

        start_time = time.time()

        if categories is None:
            categories = [5000]  # TV

        endpoint = f"{self.base_url}/api/v1/search"
        headers = self._get_headers()

        # Build query based on what's specified
        if episode is not None and season is not None:
            # Single episode search
            params = {
                "type": "tv",
                "tvdbid": f"tvdb:{tvdbid}",
                "season": season,
                "episode": episode,
            }
        elif season is not None:
            # Season pack search
            params = {
                "type": "tv",
                "tvdbid": f"tvdb:{tvdbid}",
                "season": season,
            }
        else:
            # Full series search
            params = {
                "type": "tv",
                "tvdbid": f"tvdb:{tvdbid}",
            }

        releases = []
        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(
                    endpoint,
                    headers=headers,
                    params=params,
                    timeout=60.0,
                )
                if response.status_code == 200:
                    results = response.json()
                    for result in results:
                        for release_data in result.get("releases", []):
                            releases.append(self._parse_release_info(release_data))
            except httpx.RequestError:
                pass

        return ProwlarrSearchResult(
            releases=releases,
            query_time_ms=int((time.time() - start_time) * 1000),
        )
