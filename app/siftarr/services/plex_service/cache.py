from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any, Self

import httpx

from app.siftarr.config import Settings

from .models import PlexLookupResult

if TYPE_CHECKING:
    from collections.abc import MutableMapping


class PlexServiceCacheMixin:
    settings: Settings
    base_url: str | None
    token: str | None
    _scan_cycle_depth: int
    _scan_cycle_guid_cache: MutableMapping[tuple[str, str], PlexLookupResult]
    _scan_cycle_rating_key_cache: MutableMapping[str, dict[str, Any]]
    _scan_cycle_sections_cache: MutableMapping[str, list[dict[str, Any]]]

    async def _get_client(self) -> httpx.AsyncClient:
        raise NotImplementedError

    def _get_headers(self) -> dict[str, str]:
        raise NotImplementedError

    def _is_available(self, metadata: dict[str, Any]) -> bool:
        """Check if a metadata entry has Media (is available on Plex)."""
        return "Media" in metadata and bool(metadata.get("Media"))

    @asynccontextmanager
    async def scan_cycle(self) -> AsyncIterator[Self]:
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
