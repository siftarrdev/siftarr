"""Service for fetching per-episode availability from Plex."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import httpx

from app.siftarr.config import Settings, get_settings
from app.siftarr.services.async_utils import gather_limited
from app.siftarr.services.http_client import get_shared_client

logger = logging.getLogger(__name__)

# Modern Plex agents (tv.plex.agents.series, tv.plex.agents.movie) store
# external IDs in "tmdb://ID" / "tvdb://ID" format inside each item's
# Guid array.  The legacy /library/search?guid= endpoint only accepts the
# old "com.plexapp.agents.themoviedb://ID" / "com.plexapp.agents.thetvdb://ID"
# format *and* modern Plex versions may return 400 for guid searches entirely.
#
# We try both GUID formats via /library/search?guid=, and if both fail we
# fall back to scanning all items in the relevant library section and
# matching by the Guid array.
_MODERN_GUID_PREFIXES: dict[str, list[str]] = {
    # key: search-prefix  value: list of prefixes to try, newest first
    "tmdb": ["tmdb://", "com.plexapp.agents.themoviedb://"],
    "tvdb": ["tvdb://", "com.plexapp.agents.thetvdb://"],
}


@dataclass(slots=True)
class PlexLookupResult:
    """Result for Plex lookups that can distinguish missing vs inconclusive."""

    item: dict[str, Any] | None
    authoritative: bool
    matched_guid: str | None = None
    failed_sections: tuple[str, ...] = ()


@dataclass(slots=True)
class PlexLibraryScanResult:
    """Result for a full/recent library scan with authoritative status."""

    media_type: str
    items: tuple[dict[str, Any], ...]
    authoritative: bool
    failed_sections: tuple[str, ...] = ()


@dataclass(slots=True)
class PlexEpisodeAvailabilityResult:
    """Episode availability with authoritative status."""

    availability: dict[tuple[int, int], bool]
    authoritative: bool


class PlexTransientScanError(RuntimeError):
    """Raised when a Plex section scan could not complete authoritatively."""


class PlexService:
    """Service for fetching per-episode availability from Plex."""

    def __init__(self, settings: Settings | None = None) -> None:
        """Initialize the Plex service."""
        self.settings = settings or get_settings()
        self.base_url = str(self.settings.plex_url).rstrip("/") if self.settings.plex_url else None
        self.token = self.settings.plex_token
        self._scan_cycle_depth = 0
        self._scan_cycle_guid_cache: dict[tuple[str, str], PlexLookupResult] = {}
        self._scan_cycle_rating_key_cache: dict[str, dict[str, Any]] = {}
        self._scan_cycle_sections_cache: dict[str, list[dict[str, Any]]] = {}

    def _get_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {"Accept": "application/json"}
        if self.token:
            headers["X-Plex-Token"] = self.token
        return headers

    async def _get_client(self) -> httpx.AsyncClient:
        return await get_shared_client()

    async def close(self) -> None:
        """Close the service (no-op since using shared client)."""
        pass

    def _is_available(self, metadata: dict[str, Any]) -> bool:
        """Check if a metadata entry has Media (is available on Plex)."""
        return "Media" in metadata and bool(metadata.get("Media"))

    @asynccontextmanager
    async def scan_cycle(self) -> AsyncIterator["PlexService"]:
        """Enable per-scan caches for repeated Plex lookups."""
        self._scan_cycle_depth += 1
        if self._scan_cycle_depth == 1:
            self.clear_scan_cycle_caches()
        try:
            yield self
        finally:
            self._scan_cycle_depth -= 1
            if self._scan_cycle_depth == 0:
                self.clear_scan_cycle_caches()

    def clear_scan_cycle_caches(self) -> None:
        """Clear any active per-scan caches."""
        self._scan_cycle_guid_cache.clear()
        self._scan_cycle_rating_key_cache.clear()
        self._scan_cycle_sections_cache.clear()

    def get_cached_item_by_rating_key(self, rating_key: str) -> dict[str, Any] | None:
        """Return a scan-cycle cached Plex item by rating key, if present."""
        return self._scan_cycle_rating_key_cache.get(str(rating_key))

    # ------------------------------------------------------------------
    #  Internal helpers for parsing Plex JSON responses
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_metadata_items(container: dict[str, Any]) -> list[dict[str, Any]]:
        """Extract metadata items from a Plex MediaContainer response.

        Plex returns metadata in two formats depending on endpoint:

        * Direct listings (``/library/sections/…/all``, ``/metadata/…/children``)
          use ``MediaContainer.Metadata[]``.

        * Search results (``/library/search?query=…``) nest each item inside
          ``MediaContainer.SearchResult[].Metadata``.

        This helper normalises both into a flat list of metadata dicts.
        """
        direct = container.get("Metadata")
        if isinstance(direct, list):
            return direct

        items: list[dict[str, Any]] = []
        for sr in container.get("SearchResult", []):
            md = sr.get("Metadata")
            if isinstance(md, dict):
                items.append(md)
        return items

    def _match_guid(self, item: dict[str, Any], guid_prefix: str, guid_id: int) -> bool:
        """Return True if *item* has a Guid entry matching *prefix* + *guid_id*."""
        target = f"{guid_prefix}{guid_id}"
        for g in item.get("Guid", item.get("guids", ())):
            gid = g.get("id", "") if isinstance(g, dict) else str(g)
            if gid == target:
                return True
        return False

    @staticmethod
    def _normalize_section_metadata(section: dict[str, Any]) -> dict[str, Any] | None:
        """Normalize a Plex section Directory entry."""
        key = section.get("key")
        section_type = section.get("type")
        if not key or not section_type:
            return None
        return {
            "key": str(key),
            "type": str(section_type),
            "title": section.get("title"),
            "agent": section.get("agent"),
            "scanner": section.get("scanner"),
        }

    @staticmethod
    def _extract_guid_ids(item: dict[str, Any]) -> tuple[str, ...]:
        """Extract normalized Plex guid identifiers from a metadata item."""
        guid_values = item.get("guids")
        if isinstance(guid_values, list | tuple):
            return tuple(str(g) for g in guid_values if g)

        extracted: list[str] = []
        for guid in item.get("Guid", []):
            guid_id = guid.get("id") if isinstance(guid, dict) else guid
            if guid_id:
                extracted.append(str(guid_id))
        return tuple(extracted)

    @classmethod
    def _normalize_library_item(
        cls,
        item: dict[str, Any],
        *,
        section_key: str | None = None,
    ) -> dict[str, Any] | None:
        """Normalize a Plex metadata item used by scans and lookups."""
        rating_key = item.get("rating_key") or item.get("ratingKey")
        if not rating_key:
            return None

        normalized = {
            "rating_key": str(rating_key),
            "title": item.get("title"),
            "year": item.get("year"),
            "guid": item.get("guid"),
            "Media": item.get("Media"),
            "type": item.get("type"),
            "added_at": item.get("added_at", item.get("addedAt")),
            "section_key": section_key or item.get("section_key"),
            "guids": cls._extract_guid_ids(item),
        }
        return normalized

    def _cache_lookup_result(
        self,
        media_type: str,
        guid_values: tuple[str, ...],
        result: PlexLookupResult,
    ) -> None:
        """Store a lookup result in scan-cycle caches."""
        if self._scan_cycle_depth <= 0:
            return

        if result.item is not None:
            self._cache_item(result.item, media_type=media_type)

        for guid in guid_values:
            self._scan_cycle_guid_cache[(media_type, guid)] = result

    def _cache_item(self, item: dict[str, Any], *, media_type: str | None = None) -> None:
        """Store a normalized item in scan-cycle caches."""
        if self._scan_cycle_depth <= 0:
            return

        normalized = self._normalize_library_item(item)
        if normalized is None:
            return

        resolved_media_type = str(media_type or normalized.get("type") or "")
        rating_key = normalized.get("rating_key")
        if rating_key:
            self._scan_cycle_rating_key_cache[str(rating_key)] = normalized

        if resolved_media_type:
            guid_values = tuple(
                guid
                for guid in (
                    *(normalized.get("guids") or ()),
                    normalized.get("guid"),
                )
                if guid
            )
            for guid in guid_values:
                self._scan_cycle_guid_cache[(resolved_media_type, str(guid))] = PlexLookupResult(
                    item=normalized,
                    authoritative=True,
                    matched_guid=str(guid),
                )

    def _get_cached_lookup_result(
        self,
        media_type: str,
        guid_values: tuple[str, ...],
    ) -> PlexLookupResult | None:
        """Return a cached lookup result when a scan cycle is active."""
        if self._scan_cycle_depth <= 0:
            return None

        saw_inconclusive = False
        saw_authoritative_miss = False

        for guid in guid_values:
            cached = self._scan_cycle_guid_cache.get((media_type, guid))
            if cached is None:
                return None
            if cached.item is not None:
                return cached
            if cached.authoritative:
                saw_authoritative_miss = True
            else:
                saw_inconclusive = True

        return PlexLookupResult(
            item=None,
            authoritative=saw_authoritative_miss and not saw_inconclusive,
        )

    @staticmethod
    def _section_scan_endpoint(section_key: str, *, recently_added: bool) -> str:
        """Return the endpoint suffix for a section scan."""
        section_path = "recentlyAdded" if recently_added else "all"
        return f"/library/sections/{section_key}/{section_path}"

    async def _get_library_sections_metadata(
        self,
        media_type: str,
        *,
        strict: bool,
    ) -> list[dict[str, Any]]:
        """Get normalized section metadata for a Plex library type."""
        if not self.base_url or not self.token:
            return []

        if self._scan_cycle_depth > 0 and media_type in self._scan_cycle_sections_cache:
            return list(self._scan_cycle_sections_cache[media_type])

        endpoint = f"{self.base_url}/library/sections"
        client = await self._get_client()

        try:
            response = await client.get(
                endpoint,
                headers=self._get_headers(),
                timeout=30.0,
            )
            if response.status_code != 200:
                if strict:
                    raise PlexTransientScanError(
                        f"Plex section listing failed for {media_type}: status={response.status_code}"
                    )
                return []

            data = response.json()
            container = data.get("MediaContainer", {})
            sections = [
                normalized
                for section in container.get("Directory", [])
                if (normalized := self._normalize_section_metadata(section)) is not None
                and normalized["type"] == media_type
            ]
            if self._scan_cycle_depth > 0:
                self._scan_cycle_sections_cache[media_type] = sections
            return list(sections)
        except (httpx.RequestError, ValueError) as exc:
            if strict:
                raise PlexTransientScanError(
                    f"Plex section listing failed for {media_type}: {exc}"
                ) from exc
            return []

    async def iter_section_items(
        self,
        section_key: str,
        *,
        recently_added: bool = False,
        page_size: int = 200,
    ) -> AsyncIterator[dict[str, Any]]:
        """Iterate a Plex section with pagination, yielding normalized items."""
        if not self.base_url or not self.token:
            return

        endpoint = f"{self.base_url}{self._section_scan_endpoint(section_key, recently_added=recently_added)}"
        client = await self._get_client()
        offset = 0

        while True:
            try:
                response = await client.get(
                    endpoint,
                    headers=self._get_headers(),
                    params={
                        "includeGuids": "1",
                        "X-Plex-Container-Start": str(offset),
                        "X-Plex-Container-Size": str(page_size),
                    },
                    timeout=30.0,
                )
                if response.status_code != 200:
                    raise PlexTransientScanError(
                        f"Plex section scan failed for section {section_key}: status={response.status_code}"
                    )

                data = response.json()
            except (httpx.RequestError, ValueError) as exc:
                raise PlexTransientScanError(
                    f"Plex section scan failed for section {section_key}: {exc}"
                ) from exc

            container = data.get("MediaContainer", {})
            items = self._extract_metadata_items(container)
            for item in items:
                normalized = self._normalize_library_item(item, section_key=section_key)
                if normalized is None:
                    continue
                self._cache_item(normalized)
                yield normalized

            response_size = container.get("size")
            if isinstance(response_size, str) and response_size.isdigit():
                response_size = int(response_size)
            if not isinstance(response_size, int):
                response_size = len(items)

            total_size = container.get("totalSize")
            if isinstance(total_size, str) and total_size.isdigit():
                total_size = int(total_size)

            if response_size <= 0:
                break

            offset += response_size
            if isinstance(total_size, int):
                if offset >= total_size:
                    break
            elif response_size < page_size:
                break

    async def iter_library_items(
        self,
        media_type: str,
        *,
        recently_added: bool = False,
        page_size: int = 200,
    ) -> AsyncIterator[dict[str, Any]]:
        """Iterate every item across all Plex sections for a media type."""
        sections = await self._get_library_sections_metadata(media_type, strict=True)
        for section in sections:
            async for item in self.iter_section_items(
                section["key"],
                recently_added=recently_added,
                page_size=page_size,
            ):
                yield item

    async def iter_full_library_items(
        self,
        media_type: str,
        *,
        page_size: int = 200,
    ) -> AsyncIterator[dict[str, Any]]:
        """Iterate every item from full-library Plex section scans."""
        async for item in self.iter_library_items(
            media_type,
            recently_added=False,
            page_size=page_size,
        ):
            yield item

    async def iter_recently_added_items(
        self,
        media_type: str,
        *,
        page_size: int = 200,
    ) -> AsyncIterator[dict[str, Any]]:
        """Iterate every item from recently-added Plex section scans."""
        async for item in self.iter_library_items(
            media_type,
            recently_added=True,
            page_size=page_size,
        ):
            yield item

    async def scan_library_items(
        self,
        media_type: str,
        *,
        recently_added: bool = False,
        page_size: int = 200,
    ) -> PlexLibraryScanResult:
        """Scan every relevant Plex section and report authoritative status."""
        try:
            sections = await self._get_library_sections_metadata(media_type, strict=True)
        except PlexTransientScanError:
            return PlexLibraryScanResult(
                media_type=media_type,
                items=(),
                authoritative=False,
                failed_sections=("__sections__",),
            )

        scanned_items: list[dict[str, Any]] = []
        failed_sections: list[str] = []

        for section in sections:
            section_key = section["key"]
            try:
                async for item in self.iter_section_items(
                    section_key,
                    recently_added=recently_added,
                    page_size=page_size,
                ):
                    scanned_items.append(item)
            except PlexTransientScanError:
                failed_sections.append(section_key)

        return PlexLibraryScanResult(
            media_type=media_type,
            items=tuple(scanned_items),
            authoritative=not failed_sections,
            failed_sections=tuple(failed_sections),
        )

    async def _scan_sections_for_guids(
        self,
        guid_values: tuple[str, ...],
        media_type: str,
    ) -> PlexLookupResult:
        """Scan all sections for one of the given external guid values."""
        try:
            sections = await self._get_library_sections_metadata(media_type, strict=True)
        except PlexTransientScanError:
            return PlexLookupResult(item=None, authoritative=False)

        failed_sections: list[str] = []
        for section in sections:
            section_key = section["key"]
            try:
                async for item in self.iter_section_items(section_key, page_size=200):
                    if item.get("type") != media_type:
                        continue
                    matched_guid = next(
                        (guid for guid in guid_values if guid in (item.get("guids") or ())),
                        None,
                    )
                    if matched_guid is not None:
                        return PlexLookupResult(
                            item=item,
                            authoritative=True,
                            matched_guid=matched_guid,
                            failed_sections=tuple(failed_sections),
                        )
            except PlexTransientScanError:
                failed_sections.append(section_key)

        return PlexLookupResult(
            item=None,
            authoritative=not failed_sections,
            failed_sections=tuple(failed_sections),
        )

    async def _lookup_by_external_id(
        self,
        *,
        guid_type: str,
        external_id: int,
        media_type: str,
    ) -> PlexLookupResult:
        """Lookup Plex content by external id with scan-aware caching."""
        if not self.base_url or not self.token:
            return PlexLookupResult(item=None, authoritative=True)

        guid_values = tuple(f"{prefix}{external_id}" for prefix in _MODERN_GUID_PREFIXES[guid_type])
        cached = self._get_cached_lookup_result(media_type, guid_values)
        if cached is not None:
            return cached

        for guid in guid_values:
            result = await self._search_guid(guid, media_type)
            if result is not None:
                cache_guids = tuple({*guid_values, *(result.get("guids") or ())})
                lookup_result = PlexLookupResult(
                    item=result,
                    authoritative=True,
                    matched_guid=guid,
                )
                self._cache_lookup_result(media_type, cache_guids, lookup_result)
                return lookup_result

        lookup_result = await self._scan_sections_for_guids(guid_values, media_type)
        cache_guids = guid_values
        if lookup_result.item is not None:
            cache_guids = tuple(
                {
                    *guid_values,
                    *(lookup_result.item.get("guids") or ()),
                }
            )
        self._cache_lookup_result(media_type, cache_guids, lookup_result)
        return lookup_result

    async def _find_by_guid_in_sections(
        self,
        guid_prefix: str,
        guid_id: int,
        media_type: str,
    ) -> dict[str, Any] | None:
        """Scan library sections to find an item by its external Guid.

        Used as a fallback when /library/search?guid= fails (e.g. on modern
        Plex agents that don't support guid-based search).
        """
        result = await self._scan_sections_for_guids((f"{guid_prefix}{guid_id}",), media_type)
        return result.item

    @staticmethod
    def _item_to_show_dict(item: dict[str, Any]) -> dict[str, Any]:
        """Convert a Plex metadata item to our simplified show/movie dict."""
        normalized = PlexService._normalize_library_item(item)
        if normalized is None:
            return {
                "rating_key": item.get("ratingKey"),
                "title": item.get("title"),
                "year": item.get("year"),
                "guid": item.get("guid"),
                "Media": item.get("Media"),
            }
        return {
            "rating_key": normalized.get("rating_key"),
            "title": normalized.get("title"),
            "year": normalized.get("year"),
            "guid": normalized.get("guid"),
            "Media": normalized.get("Media"),
        }

    # ------------------------------------------------------------------
    #  Public search / lookup methods
    # ------------------------------------------------------------------

    async def search_show(self, title: str) -> list[dict[str, Any]]:
        """Search Plex library by title, return matching items with rating keys.

        Args:
            title: The show title to search for.

        Returns:
            List of matching show metadata dicts with ratingKey, title, and year.
        """
        if not self.base_url or not self.token:
            return []

        endpoint = f"{self.base_url}/library/search"
        client = await self._get_client()
        params = {"query": title}

        try:
            response = await client.get(
                endpoint,
                headers=self._get_headers(),
                params=params,
                timeout=30.0,
            )
            if response.status_code == 200:
                data = response.json()
                container = data.get("MediaContainer", {})
                results = self._extract_metadata_items(container)
                matches = [
                    {
                        "rating_key": item.get("ratingKey"),
                        "title": item.get("title"),
                        "year": item.get("year"),
                        "guid": item.get("guid"),
                    }
                    for item in results
                    if item.get("type") == "show" and item.get("ratingKey")
                ]
                logger.debug(
                    "PlexService: search_show(%r) returned %d match(es)", title, len(matches)
                )
                return matches
            logger.warning(
                "PlexService: search_show(%r) returned status %d", title, response.status_code
            )
            return []
        except (httpx.RequestError, ValueError):
            logger.exception("PlexService: search_show(%r) failed", title)
            return []

    async def _search_guid(
        self,
        guid: str,
        media_type: str,
    ) -> dict[str, Any] | None:
        """Search Plex by guid string and return first matching item.

        Handles both ``Metadata[]`` and ``SearchResult[].Metadata`` response formats.
        """
        endpoint = f"{self.base_url}/library/search"
        client = await self._get_client()
        params = {"guid": guid}

        try:
            response = await client.get(
                endpoint,
                headers=self._get_headers(),
                params=params,
                timeout=30.0,
            )
            if response.status_code == 200:
                data = response.json()
                container = data.get("MediaContainer", {})
                results = self._extract_metadata_items(container)
                for item in results:
                    if item.get("type") == media_type and item.get("ratingKey"):
                        normalized = self._normalize_library_item(item)
                        if normalized is None:
                            continue
                        self._cache_item(normalized, media_type=media_type)
                        return normalized
                return None
            # guid search returning non-200 (e.g. 400) means the format
            # isn't supported by this Plex server – caller should try next.
            return None
        except (httpx.RequestError, ValueError):
            return None

    async def get_movie_by_tmdb(self, tmdb_id: int) -> dict[str, Any] | None:
        """Find a movie in Plex library by TMDB ID.

        Tries multiple guid formats (modern ``tmdb://``, legacy
        ``com.plexapp.agents.themoviedb://``) and falls back to a
        library section scan.

        Args:
            tmdb_id: The TMDB ID to search for.

        Returns:
            Movie metadata dict if found, None otherwise.
        """
        result = await self.lookup_movie_by_tmdb(tmdb_id)
        if result.item is not None:
            logger.info(
                "PlexService: get_movie_by_tmdb(%s) found: %s",
                tmdb_id,
                result.item.get("rating_key"),
            )
            return self._item_to_show_dict(result.item)

        logger.debug("PlexService: get_movie_by_tmdb(%s) found no match", tmdb_id)
        return None

    async def lookup_movie_by_tmdb(self, tmdb_id: int) -> PlexLookupResult:
        """Lookup a movie by TMDB id with authoritative status information."""
        return await self._lookup_by_external_id(
            guid_type="tmdb",
            external_id=tmdb_id,
            media_type="movie",
        )

    async def check_movie_available(self, tmdb_id: int) -> bool:
        """Check if a movie is available on Plex by TMDB ID.

        Args:
            tmdb_id: The TMDB ID to check.

        Returns:
            True if the movie exists in Plex and has Media entries.
        """
        movie = await self.get_movie_by_tmdb(tmdb_id)
        if movie is None:
            return False
        return self._is_available(movie)

    async def get_show_by_tmdb(self, tmdb_id: int) -> dict[str, Any] | None:
        """Find a show in Plex library by TMDB ID.

        Tries multiple guid formats (modern ``tmdb://``, legacy
        ``com.plexapp.agents.themoviedb://``) and falls back to a
        library section scan.

        Args:
            tmdb_id: The TMDB ID to search for.

        Returns:
            Show metadata dict if found, None otherwise.
        """
        result = await self.lookup_show_by_tmdb(tmdb_id)
        if result.item is not None:
            logger.info(
                "PlexService: get_show_by_tmdb(%s) found: %s",
                tmdb_id,
                result.item.get("rating_key"),
            )
            return self._item_to_show_dict(result.item)

        logger.debug("PlexService: get_show_by_tmdb(%s) found no match", tmdb_id)
        return None

    async def lookup_show_by_tmdb(self, tmdb_id: int) -> PlexLookupResult:
        """Lookup a show by TMDB id with authoritative status information."""
        return await self._lookup_by_external_id(
            guid_type="tmdb",
            external_id=tmdb_id,
            media_type="show",
        )

    async def get_show_by_tvdb(self, tvdb_id: int) -> dict[str, Any] | None:
        """Find a show in Plex library by TVDB ID.

        Tries multiple guid formats (modern ``tvdb://``, legacy
        ``com.plexapp.agents.thetvdb://``) and falls back to a
        library section scan.

        Args:
            tvdb_id: The TVDB ID to search for.

        Returns:
            Show metadata dict if found, None otherwise.
        """
        result = await self.lookup_show_by_tvdb(tvdb_id)
        if result.item is not None:
            logger.info(
                "PlexService: get_show_by_tvdb(%s) found: %s",
                tvdb_id,
                result.item.get("rating_key"),
            )
            return self._item_to_show_dict(result.item)

        logger.debug("PlexService: get_show_by_tvdb(%s) found no match", tvdb_id)
        return None

    async def lookup_show_by_tvdb(self, tvdb_id: int) -> PlexLookupResult:
        """Lookup a show by TVDB id with authoritative status information."""
        return await self._lookup_by_external_id(
            guid_type="tvdb",
            external_id=tvdb_id,
            media_type="show",
        )

    async def get_show_children(self, rating_key: str) -> list[dict[str, Any]]:
        """Get all seasons for a show.

        Args:
            rating_key: The Plex rating key for the show.

        Returns:
            List of season metadata dicts.
        """
        if not self.base_url or not self.token:
            return []

        endpoint = f"{self.base_url}/library/metadata/{rating_key}/children"
        client = await self._get_client()

        try:
            response = await client.get(
                endpoint,
                headers=self._get_headers(),
                timeout=30.0,
            )
            if response.status_code == 200:
                data = response.json()
                container = data.get("MediaContainer", {})
                return container.get("Metadata", [])
            logger.warning(
                "PlexService: get_show_children(%s) returned status %d",
                rating_key,
                response.status_code,
            )
            return []
        except (httpx.RequestError, ValueError):
            logger.exception("PlexService: get_show_children(%s) failed", rating_key)
            return []

    async def get_season_children(self, rating_key: str) -> list[dict[str, Any]]:
        """Get all episodes for a season.

        Args:
            rating_key: The Plex rating key for the season.

        Returns:
            List of episode metadata dicts.
        """
        if not self.base_url or not self.token:
            return []

        endpoint = f"{self.base_url}/library/metadata/{rating_key}/children"
        client = await self._get_client()

        try:
            response = await client.get(
                endpoint,
                headers=self._get_headers(),
                timeout=30.0,
            )
            if response.status_code == 200:
                data = response.json()
                container = data.get("MediaContainer", {})
                return container.get("Metadata", [])
            logger.warning(
                "PlexService: get_season_children(%s) returned status %d",
                rating_key,
                response.status_code,
            )
            return []
        except (httpx.RequestError, ValueError):
            logger.exception("PlexService: get_season_children(%s) failed", rating_key)
            return []

    async def _get_metadata_children_strict(self, rating_key: str) -> list[dict[str, Any]]:
        """Get metadata children and raise on transient Plex failures."""
        if not self.base_url or not self.token:
            return []

        endpoint = f"{self.base_url}/library/metadata/{rating_key}/children"
        client = await self._get_client()

        try:
            response = await client.get(
                endpoint,
                headers=self._get_headers(),
                timeout=30.0,
            )
            if response.status_code != 200:
                raise PlexTransientScanError(
                    f"Plex metadata children lookup failed for {rating_key}: status={response.status_code}"
                )
            data = response.json()
            container = data.get("MediaContainer", {})
            metadata = container.get("Metadata", [])
            return metadata if isinstance(metadata, list) else []
        except (httpx.RequestError, ValueError) as exc:
            raise PlexTransientScanError(
                f"Plex metadata children lookup failed for {rating_key}: {exc}"
            ) from exc

    async def get_episode_availability(self, rating_key: str) -> dict[tuple[int, int], bool]:
        """Get per-episode availability for a show.

        Queries all seasons and episodes to build a map of which episodes
        are available on Plex.

        Args:
            rating_key: The Plex rating key for the show.

        Returns:
            Dict mapping (season_number, episode_number) -> available (True/False).
        """
        seasons = await self.get_show_children(rating_key)
        logger.info(
            "PlexService: get_episode_availability(rating_key=%s) found %d season(s)",
            rating_key,
            len(seasons),
        )

        season_infos: list[tuple[int, str]] = []
        for season in seasons:
            if season.get("type") != "season":
                continue
            season_number = season.get("index")
            if season_number is None:
                continue
            season_rating_key = season.get("ratingKey")
            if not season_rating_key:
                continue

            season_infos.append((season_number, season_rating_key))

        season_episodes = await gather_limited(
            (season_rating_key for _, season_rating_key in season_infos),
            self.settings.plex_sync_concurrency,
            self.get_season_children,
        )

        availability: dict[tuple[int, int], bool] = {}
        for (season_number, _), episodes in zip(season_infos, season_episodes, strict=True):
            available_in_season = 0
            for episode in episodes:
                if episode.get("type") != "episode":
                    continue
                episode_number = episode.get("index")
                if episode_number is None:
                    continue
                is_available = self._is_available(episode)
                availability[(season_number, episode_number)] = is_available
                if is_available:
                    available_in_season += 1
            logger.debug(
                "PlexService: season %d has %d/%d available episodes",
                season_number,
                available_in_season,
                len(episodes),
            )

        total_available = sum(1 for v in availability.values() if v)
        logger.info(
            "PlexService: get_episode_availability(rating_key=%s) total %d/%d episodes available",
            rating_key,
            total_available,
            len(availability),
        )
        return availability

    async def get_episode_availability_result(
        self, rating_key: str
    ) -> PlexEpisodeAvailabilityResult:
        """Get episode availability while preserving transient-failure semantics."""
        try:
            seasons = await self._get_metadata_children_strict(rating_key)
        except PlexTransientScanError:
            return PlexEpisodeAvailabilityResult(availability={}, authoritative=False)

        season_infos: list[tuple[int, str]] = []
        for season in seasons:
            if season.get("type") != "season":
                continue
            season_number = season.get("index")
            season_rating_key = season.get("ratingKey")
            if season_number is None or not season_rating_key:
                continue
            season_infos.append((season_number, str(season_rating_key)))

        availability: dict[tuple[int, int], bool] = {}
        for season_number, season_rating_key in season_infos:
            try:
                episodes = await self._get_metadata_children_strict(season_rating_key)
            except PlexTransientScanError:
                return PlexEpisodeAvailabilityResult(availability={}, authoritative=False)

            for episode in episodes:
                if episode.get("type") != "episode":
                    continue
                episode_number = episode.get("index")
                if episode_number is None:
                    continue
                availability[(season_number, episode_number)] = self._is_available(episode)

        return PlexEpisodeAvailabilityResult(availability=availability, authoritative=True)

    async def get_all_show_rating_keys(self) -> list[str]:
        """Get all show rating keys from the library sections.

        Returns a list of all rating keys for TV shows in the Plex library.
        This is useful for discovering the rating key for a show when you
        don't already know it.

        Returns:
            List of rating key strings for TV shows.
        """
        if not self.base_url or not self.token:
            return []

        rating_keys: list[str] = []

        async for item in self.iter_full_library_items("show"):
            if item.get("type") == "show" and item.get("rating_key"):
                rating_keys.append(str(item["rating_key"]))

        return rating_keys

    async def _get_tv_library_sections(self) -> list[str]:
        """Get the library section keys for TV content."""
        sections = await self._get_library_sections_metadata("show", strict=False)
        return [str(section["key"]) for section in sections]

    async def _get_movie_library_sections(self) -> list[str]:
        """Get the library section keys for movie content."""
        sections = await self._get_library_sections_metadata("movie", strict=False)
        return [str(section["key"]) for section in sections]

    async def _get_section_shows(self, section_key: str) -> list[str]:
        """Get all show rating keys in a library section."""
        rating_keys: list[str] = []
        try:
            async for item in self.iter_section_items(section_key):
                if item.get("type") == "show" and item.get("rating_key"):
                    rating_keys.append(str(item["rating_key"]))
        except PlexTransientScanError:
            return []
        return rating_keys
