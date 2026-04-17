import logging
import re
from datetime import datetime
from typing import Any, cast

import httpx
from pydantic import BaseModel

from app.siftarr.config import Settings, get_settings
from app.siftarr.services.http_client import get_shared_client

logger = logging.getLogger(__name__)


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
    error: str | None = None


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

    @staticmethod
    def _build_movie_query(title: str | None, tmdbid: int, year: int | None = None) -> str:
        """Build a Prowlarr movie query with metadata tokens in the query string."""
        parts = [title.strip() for title in [title] if title and title.strip()]
        parts.append(f"{{tmdbid:{tmdbid}}}")
        if year is not None:
            parts.append(f"{{year:{year}}}")
        return " ".join(parts)

    @staticmethod
    def _build_movie_title_query(title: str | None, year: int | None = None) -> str:
        """Build a plain title-based movie query for fallback searches."""
        parts = [title.strip() for title in [title] if title and title.strip()]
        if year is not None:
            parts.append(str(year))
        return " ".join(parts)

    @staticmethod
    def _build_tv_query(
        title: str | None,
        tvdbid: int,
        season: int | None = None,
        episode: int | None = None,
        year: int | None = None,
    ) -> str:
        """Build a Prowlarr TV query with metadata tokens in the query string."""
        parts = [title.strip() for title in [title] if title and title.strip()]
        parts.append(f"{{tvdbid:{tvdbid}}}")
        if season is not None:
            parts.append(f"{{season:{season}}}")
        if episode is not None:
            parts.append(f"{{episode:{episode}}}")
        if year is not None:
            parts.append(f"{{year:{year}}}")
        return " ".join(parts)

    @staticmethod
    def _build_tv_title_query(
        title: str | None,
        season: int | None = None,
        episode: int | None = None,
        year: int | None = None,
    ) -> str:
        """Build a plain title-based TV query for fallback searches."""
        parts = [title.strip() for title in [title] if title and title.strip()]
        if season is not None and episode is not None:
            parts.append(f"S{season:02d}E{episode:02d}")
        elif season is not None:
            parts.append(f"S{season:02d}")
        if year is not None:
            parts.append(str(year))
        return " ".join(parts)

    async def _search(
        self,
        params: dict,
    ) -> ProwlarrSearchResult:
        """Execute a Prowlarr search request and normalize results."""
        import time

        start_time = time.time()
        endpoint = f"{self.base_url}/api/v1/search"
        headers = self._get_headers()

        logger.info(
            "Prowlarr search request: type=%s query=%s categories=%s",
            params.get("type"),
            params.get("query"),
            params.get("categories"),
        )

        releases = []
        error_message = None
        client = await get_shared_client()
        try:
            response = await client.get(
                endpoint,
                headers=headers,
                params=params,
                timeout=60.0,
            )
            if response.status_code == 200:
                results = response.json()
                for release_data in self._extract_release_items(results):
                    releases.append(self._parse_release_info(release_data))
                logger.info(
                    "Prowlarr search response: type=%s query=%s releases=%s elapsed_ms=%s",
                    params.get("type"),
                    params.get("query"),
                    len(releases),
                    int((time.time() - start_time) * 1000),
                )
            else:
                error_message = f"HTTP {response.status_code}"
                logger.warning(
                    "Prowlarr search failed: type=%s query=%s status_code=%s",
                    params.get("type"),
                    params.get("query"),
                    response.status_code,
                )
        except httpx.RequestError as e:
            error_message = f"Request error: {e}"
            logger.exception(
                "Prowlarr search request error: type=%s query=%s",
                params.get("type"),
                params.get("query"),
            )

        return ProwlarrSearchResult(
            releases=releases,
            query_time_ms=int((time.time() - start_time) * 1000),
            error=error_message,
        )

    @staticmethod
    def _extract_release_items(payload: object) -> list[dict[str, Any]]:
        """Normalize different Prowlarr search response shapes into release items."""
        if not isinstance(payload, list):
            return []

        releases: list[dict[str, Any]] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            item = cast(dict[str, Any], item)
            nested_releases = item.get("releases")
            if isinstance(nested_releases, list):
                releases.extend(r for r in nested_releases if isinstance(r, dict))
                continue
            if item.get("title") and (
                item.get("downloadUrl") or item.get("guid") or item.get("magnetUrl")
            ):
                releases.append(item)

        return releases

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
            (r"x265|265|HEVC", "x265"),
            (r"x264|264|AVC", "x264"),
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
        title: str | None = None,
        year: int | None = None,
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
        if categories is None:
            categories = [2000]  # Movies

        metadata_params = {
            "type": "movie",
            "query": self._build_movie_query(title, tmdbid, year),
            "categories": categories,
        }
        metadata_result = await self._search(metadata_params)
        if metadata_result.releases or not title:
            return metadata_result

        fallback_params = {
            "type": "search",
            "query": self._build_movie_title_query(title, year),
            "categories": categories,
        }
        fallback_result = await self._search(fallback_params)
        fallback_result.query_time_ms += metadata_result.query_time_ms
        return fallback_result

    async def search_by_tvdbid(
        self,
        tvdbid: int,
        title: str | None = None,
        season: int | None = None,
        episode: int | None = None,
        year: int | None = None,
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
        if categories is None:
            categories = [5000]  # TV

        metadata_params = {
            "type": "tvsearch",
            "query": self._build_tv_query(title, tvdbid, season, episode, year),
            "categories": categories,
        }
        metadata_result = await self._search(metadata_params)
        if metadata_result.releases or not title:
            return metadata_result

        # Broad search (no season, no episode) requires multiple query strategies
        if season is None and episode is None:
            return await self._broad_tv_search(
                title, tvdbid, year, categories, metadata_result.query_time_ms
            )

        fallback_params = {
            "type": "search",
            "query": self._build_tv_title_query(title, season, episode, year),
            "categories": categories,
        }
        fallback_result = await self._search(fallback_params)
        fallback_result.query_time_ms += metadata_result.query_time_ms
        return fallback_result

    async def _broad_tv_search(
        self,
        title: str,
        tvdbid: int,
        year: int | None,
        categories: list[int],
        metadata_query_time_ms: int,
    ) -> ProwlarrSearchResult:
        """Execute multiple query strategies for broad TV searches and aggregate results.

        Args:
            title: Show title
            tvdbid: TVDB ID (unused in title queries but kept for API parity)
            year: Optional year
            categories: Category IDs
            metadata_query_time_ms: Time spent on metadata query

        Returns:
            ProwlarrSearchResult with all unique releases found
        """
        # Track seen releases by download_url to avoid duplicates
        seen_urls: set[str] = set()
        all_releases: list[ProwlarrRelease] = []
        total_query_time_ms = metadata_query_time_ms

        # Query strategies for broad TV searches
        title_queries = [
            f"{title} S01-".strip(),  # e.g. "The Mentalist S01-"
            f"{title} complete".strip(),  # e.g. "The Mentalist complete"
            f"{title} season 1-".strip(),  # e.g. "The Mentalist season 1-"
        ]

        for query in title_queries:
            params = {
                "type": "search",
                "query": query,
                "categories": categories,
            }
            result = await self._search(params)
            total_query_time_ms += result.query_time_ms

            # Add unique releases
            for release in result.releases:
                if release.download_url not in seen_urls:
                    seen_urls.add(release.download_url)
                    all_releases.append(release)

        return ProwlarrSearchResult(
            releases=all_releases,
            query_time_ms=total_query_time_ms,
            error=None if all_releases else "No releases found",
        )
