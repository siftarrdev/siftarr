import asyncio
import hashlib
import json
import logging
import re
import time as time_module
from collections import OrderedDict
from datetime import datetime
from typing import Any, cast

import httpx
from pydantic import BaseModel

from app.siftarr.config import Settings, get_settings
from app.siftarr.services.utils.http_client import get_shared_client

logger = logging.getLogger(__name__)

# ── Search result cache ──────────────────────────────────────────────
# LRU cache keyed by params hash, with TTL.  Only caches non-manual
# (automatic decision pipeline) searches.
_search_cache: OrderedDict[str, tuple[float, ProwlarrSearchResult]] = OrderedDict()
_SEARCH_CACHE_MAX_SIZE = 50
_SEARCH_CACHE_TTL = 45  # seconds


def _search_cache_key(params: dict) -> str:
    """Deterministic cache key from a search-params dict."""
    raw = json.dumps(params, sort_keys=True, default=str)
    return hashlib.md5(raw.encode()).hexdigest()


def _cache_get(key: str) -> ProwlarrSearchResult | None:
    if key not in _search_cache:
        return None
    timestamp, result = _search_cache[key]
    if time_module.monotonic() - timestamp > _SEARCH_CACHE_TTL:
        del _search_cache[key]
        return None
    # Move to end (most-recently used)
    _search_cache.move_to_end(key)
    return result


def _cache_set(key: str, result: ProwlarrSearchResult) -> None:
    if len(_search_cache) >= _SEARCH_CACHE_MAX_SIZE:
        _search_cache.popitem(last=False)  # evict LRU
    _search_cache[key] = (time_module.monotonic(), result)


def clear_search_cache() -> None:
    """Invalidate the entire search result cache."""
    _search_cache.clear()


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
    uploaded_by: str | None = None
    files: int | None = None


class ProwlarrSearchResult(BaseModel):
    """Result from Prowlarr search."""

    releases: list[ProwlarrRelease]
    query_time_ms: int
    error: str | None = None
    offset: int | None = None
    page_size: int | None = None
    page_count: int | None = None
    is_short_page: bool | None = None
    query_strategy: str | None = None
    source: str | None = None
    hit_limit: bool = False


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

    @staticmethod
    def _release_page_signature(
        releases: list[ProwlarrRelease],
    ) -> tuple[tuple[str, str, str, int], ...]:
        """Return a stable signature for detecting repeated paginated pages."""
        return tuple(
            (
                release.download_url or release.magnet_url or release.info_hash or release.title,
                release.title,
                release.indexer,
                release.size,
            )
            for release in releases
        )

    @staticmethod
    def _release_key(release: ProwlarrRelease) -> str:
        """Return a stable release key for sweep-level deduplication."""
        if release.info_hash:
            return f"ih:{release.info_hash.lower()}"
        if release.download_url:
            return f"url:{release.download_url.lower()}"
        if release.magnet_url:
            return f"mag:{release.magnet_url.lower()}"
        return f"title:{release.indexer.lower()}:{release.title.lower()}:{release.size}"

    async def close(self) -> None:
        """Close the service (no-op since using shared client)."""
        pass

    def _parse_release_info(self, release: dict) -> ProwlarrRelease:
        """Parse a release from Prowlarr response."""
        # Parse title for resolution, codec, release group
        title = release.get("title", "")
        resolution = self._extract_resolution(title)
        codec = self._extract_codec(title)
        release_group = release.get("releaseGroup") or self._extract_release_group(title)
        uploaded_by = release.get("uploadedBy") or release.get("uploader")
        files = (
            release.get("files")
            or release.get("fileCount")
            or release.get("filesCount")
            or release.get("file_count")
            or release.get("numFiles")
            or release.get("numberOfFiles")
        )

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
            uploaded_by=uploaded_by,
            files=files,
        )

    @staticmethod
    def _normalize_search_title(title: str) -> str:
        """Strip apostrophes and normalize whitespace for broader indexer matching."""
        normalized = title.replace("'", "")
        return " ".join(normalized.split())

    @staticmethod
    def _build_movie_query(title: str | None, tmdbid: int, year: int | None = None) -> str:
        """Build a Prowlarr movie query with metadata tokens in the query string."""
        if title:
            title = ProwlarrService._normalize_search_title(title)
        parts = [title.strip() for title in [title] if title and title.strip()]
        parts.append(f"{{tmdbid:{tmdbid}}}")
        if year is not None:
            parts.append(f"{{year:{year}}}")
        return " ".join(parts)

    @staticmethod
    def _build_movie_title_query(title: str | None, year: int | None = None) -> str:
        """Build a plain title-based movie query for fallback searches."""
        if title:
            title = ProwlarrService._normalize_search_title(title)
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
        if title:
            title = ProwlarrService._normalize_search_title(title)
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
        if title:
            title = ProwlarrService._normalize_search_title(title)
        parts = [title.strip() for title in [title] if title and title.strip()]
        if season is not None and episode is not None:
            parts.append(f"S{season:02d}E{episode:02d}")
        elif season is not None:
            parts.append(f"S{season:02d}")
        if year is not None:
            parts.append(str(year))
        return " ".join(parts)

    @staticmethod
    def _build_tv_imdb_season_query(title: str | None, imdbid: str | int, season: int) -> str:
        """Build an IPTorrents-compatible IMDb + season TV query."""
        if title:
            title = ProwlarrService._normalize_search_title(title)
        imdb = str(imdbid).removeprefix("tt")
        parts = [title.strip() for title in [title] if title and title.strip()]
        parts.extend([f"{{imdbid:{imdb}}}", f"{{season:{season}}}"])
        return " ".join(parts)

    @staticmethod
    def _build_tv_title_season_token_query(title: str | None, season: int) -> str:
        """Build an IPTorrents-compatible title + {season:N} TV query."""
        if title:
            title = ProwlarrService._normalize_search_title(title)
        parts = [title.strip() for title in [title] if title and title.strip()]
        parts.append(f"{{season:{season}}}")
        return " ".join(parts)

    async def _search(
        self,
        params: dict,
        cacheable: bool = True,
    ) -> ProwlarrSearchResult:
        """Execute a Prowlarr search request and normalize results.

        Args:
            params: Prowlarr API search parameters.
            cacheable: If True (default), the result may be cached and
                a cached response may be returned.  Manual/dashboard
                searches should pass ``cacheable=False``.
        """
        start_time = time_module.time()
        endpoint = f"{self.base_url}/api/v1/search"
        headers = self._get_headers()

        # ── Cache check ──────────────────────────────────────────────
        if cacheable and not self.settings.siftarr_disable_search_cache:
            cache_key = _search_cache_key(params)
            cached = _cache_get(cache_key)
            if cached is not None:
                logger.info(
                    "Prowlarr search results loaded: source=cache type=%s query=%s categories=%s count=%s",
                    params.get("type"),
                    params.get("query"),
                    params.get("categories"),
                    len(cached.releases),
                )
                return cached.model_copy(update={"source": "cache"})

        logger.debug(
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
                logger.debug(
                    "Prowlarr search response: type=%s query=%s releases=%s elapsed_ms=%s",
                    params.get("type"),
                    params.get("query"),
                    len(releases),
                    int((time_module.time() - start_time) * 1000),
                )

                # ── Cache successful responses only ──────────────────
                if cacheable and not self.settings.siftarr_disable_search_cache:
                    result_to_cache = ProwlarrSearchResult(
                        releases=releases,
                        query_time_ms=int((time_module.time() - start_time) * 1000),
                        error=None,
                        source="prowlarr",
                    )
                    _cache_set(cache_key, result_to_cache)
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
            query_time_ms=int((time_module.time() - start_time) * 1000),
            error=error_message,
            source="prowlarr",
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
        except ValueError, AttributeError:
            return None

    async def search_by_tmdbid(
        self,
        tmdbid: int,
        title: str | None = None,
        year: int | None = None,
        categories: list[int] | None = None,
        cacheable: bool = True,
    ) -> ProwlarrSearchResult:
        """
        Search for movie releases by TMDB ID.

        Args:
            tmdbid: The TMDB ID to search for
            categories: Optional list of category IDs (default: [2000] for movies)
            cacheable: Whether the result may be cached (default True).
                Pass False for manual/dashboard searches.

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
        metadata_result = await self._search(metadata_params, cacheable=cacheable)
        if metadata_result.releases or not title:
            return metadata_result

        fallback_params = {
            "type": "search",
            "query": self._build_movie_title_query(title, year),
            "categories": categories,
        }
        fallback_result = await self._search(fallback_params, cacheable=cacheable)
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
        cacheable: bool = True,
    ) -> ProwlarrSearchResult:
        """
        Search for TV releases by TVDB ID.

        Args:
            tvdbid: The TVDB ID to search for
            season: Optional season number (for season pack)
            episode: Optional episode number (for single episode)
            categories: Optional list of category IDs (default: [5000] for TV)
            cacheable: Whether the result may be cached (default True).
                Pass False for manual/dashboard searches.

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
        metadata_result = await self._search(metadata_params, cacheable=cacheable)
        if metadata_result.releases or not title:
            return metadata_result

        # Broad search (no season, no episode) requires multiple query strategies
        if season is None and episode is None:
            return await self._broad_tv_search(
                title,
                tvdbid,
                year,
                categories,
                metadata_result.query_time_ms,
                cacheable=cacheable,
            )

        fallback_params = {
            "type": "search",
            "query": self._build_tv_title_query(title, season, episode, year),
            "categories": categories,
        }
        fallback_result = await self._search(fallback_params, cacheable=cacheable)
        fallback_result.query_time_ms += metadata_result.query_time_ms
        return fallback_result

    def _tv_season_strategy_queries(
        self,
        title: str,
        season: int,
        imdbid: str | int | None = None,
        tvdbid: int | None = None,
    ) -> list[tuple[str, str, str]]:
        """Return enabled TV season query strategies as (name, type, query)."""
        strategies: list[tuple[str, str, str]] = []
        if self.settings.prowlarr_tv_strategy_title_season_token_enabled:
            strategies.append(
                (
                    "title_season_token",
                    "tvsearch",
                    self._build_tv_title_season_token_query(title, season),
                )
            )
        if self.settings.prowlarr_tv_strategy_title_sxx_enabled:
            strategies.append(("title_sxx", "search", self._build_tv_title_query(title, season)))
        if imdbid and self.settings.prowlarr_tv_strategy_imdb_enabled:
            strategies.append(
                ("imdb_season", "tvsearch", self._build_tv_imdb_season_query(title, imdbid, season))
            )
        if tvdbid and self.settings.prowlarr_tv_strategy_tvdb_enabled:
            strategies.append(
                ("tvdb_season", "tvsearch", self._build_tv_query(title, tvdbid, season))
            )
        return strategies

    async def search_tv_season_page(
        self,
        title: str,
        season: int,
        strategy: str,
        offset: int = 0,
        imdbid: str | int | None = None,
        tvdbid: int | None = None,
        categories: list[int] | None = None,
        cacheable: bool = True,
        request_id: int | None = None,
    ) -> ProwlarrSearchResult:
        """Search one TV season strategy page with caller-controlled offset."""
        if categories is None:
            categories = [5000]

        strategy_map = {
            name: (search_type, query)
            for name, search_type, query in self._tv_season_strategy_queries(
                title, season, imdbid=imdbid, tvdbid=tvdbid
            )
        }
        if strategy not in strategy_map:
            return ProwlarrSearchResult(
                releases=[],
                query_time_ms=0,
                error=f"TV season strategy unavailable: {strategy}",
                offset=offset,
                page_size=self.settings.prowlarr_tv_page_size,
                page_count=0,
                is_short_page=True,
                query_strategy=strategy,
            )

        search_type, query = strategy_map[strategy]
        # Confirmed with IPTorrents via Prowlarr: the indexer returns pages of up
        # to 100 releases selected by ``offset`` and ignores/does not need a
        # caller-provided ``limit``.  Keep the request offset-only so Siftarr's
        # page size setting controls offset increments and short-page stopping.
        params = {
            "type": search_type,
            "query": query,
            "categories": categories,
            "offset": offset,
        }
        result = await self._search(params, cacheable=cacheable)
        page_size = self.settings.prowlarr_tv_page_size
        result.offset = offset
        result.page_size = page_size
        result.page_count = len(result.releases)
        result.is_short_page = len(result.releases) < page_size
        result.query_strategy = strategy
        logger.info(
            "TV season page received: request_id=%s title=%s season=%s strategy=%s offset=%s count=%s page_size=%s short_page=%s source=%s",
            request_id,
            title,
            season,
            strategy,
            offset,
            len(result.releases),
            page_size,
            result.is_short_page,
            result.source or "prowlarr",
        )
        return result

    async def search_tv_season_sweep(
        self,
        title: str,
        season: int,
        imdbid: str | int | None = None,
        tvdbid: int | None = None,
        categories: list[int] | None = None,
        cacheable: bool = True,
        request_id: int | None = None,
    ) -> ProwlarrSearchResult:
        """Run paginated TV season strategy searches until Prowlarr is exhausted."""
        if categories is None:
            categories = [5000]

        page_size = self.settings.prowlarr_tv_page_size
        max_pages = self.settings.prowlarr_tv_max_pages_per_strategy
        total_query_time_ms = 0
        all_releases: list[ProwlarrRelease] = []
        pages_searched = 0
        hit_limit = False
        seen_release_keys: set[str] = set()

        logger.info(
            "TV season sweep started: request_id=%s title=%s season=%s source=prowlarr page_size=%s",
            request_id,
            title,
            season,
            page_size,
        )

        for strategy, _, _ in self._tv_season_strategy_queries(
            title, season, imdbid=imdbid, tvdbid=tvdbid
        ):
            page = 0
            seen_page_signatures: set[tuple[tuple[str, str, str, int], ...]] = set()
            while pages_searched < max_pages:
                result = await self.search_tv_season_page(
                    title,
                    season,
                    strategy,
                    offset=page * page_size,
                    imdbid=imdbid,
                    tvdbid=tvdbid,
                    categories=categories,
                    cacheable=cacheable,
                    request_id=request_id,
                )
                pages_searched += 1
                total_query_time_ms += result.query_time_ms
                page_signature = self._release_page_signature(result.releases)
                if result.releases and page_signature in seen_page_signatures:
                    logger.warning(
                        "TV season sweep stopped on repeated page: request_id=%s title=%s season=%s strategy=%s offset=%s count=%s source=%s",
                        request_id,
                        title,
                        season,
                        strategy,
                        result.offset,
                        len(result.releases),
                        result.source or "prowlarr",
                    )
                    break
                seen_page_signatures.add(page_signature)

                new_releases: list[ProwlarrRelease] = []
                for release in result.releases:
                    release_key = self._release_key(release)
                    if release_key in seen_release_keys:
                        continue
                    seen_release_keys.add(release_key)
                    new_releases.append(release)

                if result.releases and not new_releases:
                    logger.warning(
                        "TV season sweep stopped on page with no new releases: request_id=%s title=%s season=%s strategy=%s offset=%s count=%s source=%s",
                        request_id,
                        title,
                        season,
                        strategy,
                        result.offset,
                        len(result.releases),
                        result.source or "prowlarr",
                    )
                    break

                all_releases.extend(new_releases)
                if result.error or result.is_short_page:
                    break
                page += 1
            else:
                hit_limit = True
                logger.warning(
                    "TV season sweep stopped at max pages: request_id=%s title=%s season=%s strategy=%s max_pages=%s pages_searched=%s page_size=%s source=prowlarr",
                    request_id,
                    title,
                    season,
                    strategy,
                    max_pages,
                    pages_searched,
                    page_size,
                )
                break

        logger.info(
            "TV season sweep done: request_id=%s title=%s season=%s total_results=%s elapsed_ms=%s source=prowlarr",
            request_id,
            title,
            season,
            len(all_releases),
            total_query_time_ms,
        )
        return ProwlarrSearchResult(
            releases=all_releases,
            query_time_ms=total_query_time_ms,
            error=None if all_releases else "No releases found",
            page_size=page_size,
            page_count=len(all_releases),
            source="prowlarr",
            hit_limit=hit_limit,
        )

    async def _broad_tv_search(
        self,
        title: str,
        tvdbid: int,
        year: int | None,
        categories: list[int],
        metadata_query_time_ms: int,
        cacheable: bool = True,
    ) -> ProwlarrSearchResult:
        """Execute multiple query strategies concurrently and aggregate results.

        Runs up to 3 title queries concurrently, deduplicates by
        ``download_url``, and gracefully handles individual query failures.

        Args:
            title: Show title
            tvdbid: TVDB ID (unused in title queries but kept for API parity)
            year: Optional year
            categories: Category IDs
            metadata_query_time_ms: Time spent on metadata query
            cacheable: Whether individual searches may use cached results.

        Returns:
            ProwlarrSearchResult with all unique releases found
        """
        # Normalize title and build query strategies
        title = self._normalize_search_title(title)
        title_queries = [
            f"{title} S01-".strip(),  # e.g. "The Mentalist S01-"
            f"{title} complete".strip(),  # e.g. "The Mentalist complete"
            f"{title} season 1-".strip(),  # e.g. "The Mentalist season 1-"
        ]

        semaphore = asyncio.Semaphore(3)
        seen_urls: set[str] = set()
        all_releases: list[ProwlarrRelease] = []
        total_query_time_ms = metadata_query_time_ms

        async def _search_single(query: str) -> ProwlarrSearchResult:
            async with semaphore:
                params = {
                    "type": "search",
                    "query": query,
                    "categories": categories,
                }
                return await self._search(params, cacheable=cacheable)

        results = await asyncio.gather(
            *(_search_single(q) for q in title_queries),
            return_exceptions=True,
        )

        for result in results:
            if not isinstance(result, ProwlarrSearchResult):
                logger.warning("Broad TV search query failed: %s", result)
                continue
            total_query_time_ms += result.query_time_ms

            for release in result.releases:
                if release.download_url not in seen_urls:
                    seen_urls.add(release.download_url)
                    all_releases.append(release)

        return ProwlarrSearchResult(
            releases=all_releases,
            query_time_ms=total_query_time_ms,
            error=None if all_releases else "No releases found",
        )
