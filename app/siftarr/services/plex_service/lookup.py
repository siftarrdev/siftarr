import logging
from typing import Any

import httpx

from .library_scan import PlexServiceLibraryScanMixin
from .models import _MODERN_GUID_PREFIXES, PlexLookupResult, PlexTransientScanError

logger = logging.getLogger(__name__)


class PlexServiceLookupMixin(PlexServiceLibraryScanMixin):
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

    @classmethod
    def _item_to_show_dict(cls, item: dict[str, Any]) -> dict[str, Any]:
        """Convert a Plex metadata item to our simplified show/movie dict."""
        normalized = cls._normalize_library_item(item)
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
