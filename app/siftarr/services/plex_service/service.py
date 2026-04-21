import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx

from app.siftarr.config import Settings, get_settings
from app.siftarr.services.http_client import get_shared_client

from .cache import PlexServiceCache
from .episodes import PlexServiceEpisodes
from .library_scan import PlexServiceLibraryScan
from .lookup import PlexServiceLookup

logger = logging.getLogger(__name__)


class PlexService:
    """Service for fetching per-episode availability from Plex."""

    _extract_metadata_items = staticmethod(PlexServiceCache._extract_metadata_items)
    _section_scan_endpoint = staticmethod(PlexServiceCache._section_scan_endpoint)

    def __init__(self, settings: Settings | None = None) -> None:
        """Initialize the Plex service."""
        self.settings = settings or get_settings()
        self.base_url = str(self.settings.plex_url).rstrip("/") if self.settings.plex_url else None
        self.token = self.settings.plex_token

        self._cache = PlexServiceCache()
        self._library_scan = PlexServiceLibraryScan(self, self._cache)
        self._lookup = PlexServiceLookup(self, self._cache)
        self._episodes = PlexServiceEpisodes(self, self._cache)

    def _get_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {"Accept": "application/json"}
        if self.token:
            headers["X-Plex-Token"] = self.token
        return headers

    async def _get_client(self) -> httpx.AsyncClient:
        return await get_shared_client()

    def _is_available(self, metadata: dict[str, Any]) -> bool:
        return self._cache._is_available(metadata)

    def _match_guid(self, item: dict[str, Any], guid_prefix: str, guid_id: int) -> bool:
        return self._cache._match_guid(item, guid_prefix, guid_id)

    @classmethod
    def _normalize_library_item(
        cls,
        item: dict[str, Any],
        *,
        section_key: str | None = None,
    ) -> dict[str, Any] | None:
        return PlexServiceCache._normalize_library_item(item, section_key=section_key)

    @classmethod
    def _item_to_show_dict(cls, item: dict[str, Any]) -> dict[str, Any]:
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

    @asynccontextmanager
    async def scan_cycle(self) -> AsyncIterator["PlexService"]:
        async with self._cache.scan_cycle():
            yield self

    def clear_scan_cycle_caches(self) -> None:
        self._cache.clear_scan_cycle_caches()

    def get_cached_item_by_rating_key(self, rating_key: str) -> dict[str, Any] | None:
        return self._cache.get_cached_item_by_rating_key(rating_key)

    async def _get_library_sections_metadata(
        self,
        media_type: str,
        *,
        strict: bool,
    ) -> list[dict[str, Any]]:
        return await self._library_scan._get_library_sections_metadata(media_type, strict=strict)

    async def iter_section_items(
        self,
        section_key: str,
        *,
        recently_added: bool = False,
        page_size: int = 200,
    ) -> AsyncIterator[dict[str, Any]]:
        async for item in self._library_scan.iter_section_items(
            section_key,
            recently_added=recently_added,
            page_size=page_size,
        ):
            yield item

    async def iter_library_items(
        self,
        media_type: str,
        *,
        recently_added: bool = False,
        page_size: int = 200,
    ) -> AsyncIterator[dict[str, Any]]:
        async for item in self._library_scan.iter_library_items(
            media_type,
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
        async for item in self._library_scan.iter_full_library_items(media_type, page_size=page_size):
            yield item

    async def iter_recently_added_items(
        self,
        media_type: str,
        *,
        page_size: int = 200,
    ) -> AsyncIterator[dict[str, Any]]:
        async for item in self._library_scan.iter_recently_added_items(media_type, page_size=page_size):
            yield item

    async def scan_library_items(
        self,
        media_type: str,
        *,
        recently_added: bool = False,
        page_size: int = 200,
    ):
        return await self._library_scan.scan_library_items(
            media_type,
            recently_added=recently_added,
            page_size=page_size,
        )

    async def get_all_show_rating_keys(self) -> list[str]:
        return await self._library_scan.get_all_show_rating_keys()

    async def _get_tv_library_sections(self) -> list[str]:
        return await self._library_scan._get_tv_library_sections()

    async def _get_movie_library_sections(self) -> list[str]:
        return await self._library_scan._get_movie_library_sections()

    async def _get_section_shows(self, section_key: str) -> list[str]:
        return await self._library_scan._get_section_shows(section_key)

    async def _scan_sections_for_guids(
        self,
        guid_values: tuple[str, ...],
        media_type: str,
    ):
        return await self._lookup._scan_sections_for_guids(guid_values, media_type)

    async def _lookup_by_external_id(
        self,
        *,
        guid_type: str,
        external_id: int,
        media_type: str,
    ):
        return await self._lookup._lookup_by_external_id(
            guid_type=guid_type,
            external_id=external_id,
            media_type=media_type,
        )

    async def _find_by_guid_in_sections(
        self,
        guid_prefix: str,
        guid_id: int,
        media_type: str,
    ) -> dict[str, Any] | None:
        return await self._lookup._find_by_guid_in_sections(guid_prefix, guid_id, media_type)

    async def search_show(self, title: str) -> list[dict[str, Any]]:
        return await self._lookup.search_show(title)

    async def _search_guid(self, guid: str, media_type: str) -> dict[str, Any] | None:
        return await self._lookup._search_guid(guid, media_type)

    async def get_movie_by_tmdb(self, tmdb_id: int) -> dict[str, Any] | None:
        return await self._lookup.get_movie_by_tmdb(tmdb_id)

    async def lookup_movie_by_tmdb(self, tmdb_id: int):
        return await self._lookup.lookup_movie_by_tmdb(tmdb_id)

    async def check_movie_available(self, tmdb_id: int) -> bool:
        return await self._lookup.check_movie_available(tmdb_id)

    async def get_show_by_tmdb(self, tmdb_id: int) -> dict[str, Any] | None:
        return await self._lookup.get_show_by_tmdb(tmdb_id)

    async def lookup_show_by_tmdb(self, tmdb_id: int):
        return await self._lookup.lookup_show_by_tmdb(tmdb_id)

    async def get_show_by_tvdb(self, tvdb_id: int) -> dict[str, Any] | None:
        return await self._lookup.get_show_by_tvdb(tvdb_id)

    async def lookup_show_by_tvdb(self, tvdb_id: int):
        return await self._lookup.lookup_show_by_tvdb(tvdb_id)

    async def get_show_children(self, rating_key: str) -> list[dict[str, Any]]:
        return await self._episodes.get_show_children(rating_key)

    async def get_season_children(self, rating_key: str) -> list[dict[str, Any]]:
        return await self._episodes.get_season_children(rating_key)

    async def _get_metadata_children_strict(self, rating_key: str) -> list[dict[str, Any]]:
        return await self._episodes._get_metadata_children_strict(rating_key)

    async def get_episode_availability(self, rating_key: str) -> dict[tuple[int, int], bool]:
        return await self._episodes.get_episode_availability(rating_key)

    async def get_episode_availability_result(self, rating_key: str):
        return await self._episodes.get_episode_availability_result(rating_key)

    async def close(self) -> None:
        """Close the service (no-op since using shared client)."""
        pass
