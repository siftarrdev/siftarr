import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx

from app.siftarr.config import Settings, get_settings
from app.siftarr.services.utils.http_client import get_shared_client

from .cache import PlexServiceCache
from .models import (
    _MODERN_GUID_PREFIXES,
    PlexEpisodeAvailabilityResult,
    PlexLookupResult,
    PlexTransientScanError,
)

logger = logging.getLogger(__name__)


class PlexService:
    """Service for fetching per-episode availability from Plex."""

    def __init__(self, settings: Settings | None = None) -> None:
        """Initialize the Plex service."""
        self.settings = settings or get_settings()
        self.base_url = str(self.settings.plex_url).rstrip("/") if self.settings.plex_url else None
        self.token = self.settings.plex_token

        self._cache = PlexServiceCache()

    def _get_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {"Accept": "application/json"}
        if self.token:
            headers["X-Plex-Token"] = self.token
        return headers

    async def _get_client(self) -> httpx.AsyncClient:
        return await get_shared_client()

    @classmethod
    def _item_to_show_dict(cls, item: dict[str, Any]) -> dict[str, Any]:
        normalized = PlexServiceCache._normalize_library_item(item)
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
    async def scan_cycle(self) -> AsyncIterator[PlexService]:
        async with self._cache.scan_cycle():
            yield self

    def clear_scan_cycle_caches(self) -> None:
        self._cache.clear_scan_cycle_caches()

    async def _get_library_sections_metadata(
        self,
        media_type: str,
        *,
        strict: bool,
    ) -> list[dict[str, Any]]:
        """Get normalized section metadata for a Plex library type."""
        if not self.base_url or not self.token:
            return []

        cached = self._cache.get_cached_sections(media_type)
        if cached is not None:
            return cached

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
                if (normalized := self._cache._normalize_section_metadata(section)) is not None
                and normalized["type"] == media_type
            ]
            self._cache.set_cached_sections(media_type, sections)
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

        endpoint = (
            f"{self.base_url}"
            f"{PlexServiceCache._section_scan_endpoint(section_key, recently_added=recently_added)}"
        )
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
            items = PlexServiceCache._extract_metadata_items(container)
            for item in items:
                normalized = PlexServiceCache._normalize_library_item(item, section_key=section_key)
                if normalized is None:
                    continue
                self._cache._cache_item(normalized)
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
        cached = self._cache._get_cached_lookup_result(media_type, guid_values)
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
                self._cache._cache_lookup_result(media_type, cache_guids, lookup_result)
                return lookup_result

        lookup_result = await self._scan_sections_for_guids(guid_values, media_type)
        cache_guids = guid_values
        if lookup_result.item is not None:
            cache_guids = tuple({*guid_values, *(lookup_result.item.get("guids") or ())})
        self._cache._cache_lookup_result(media_type, cache_guids, lookup_result)
        return lookup_result

    async def search_show(self, title: str) -> list[dict[str, Any]]:
        """Search Plex library by title, return matching items with rating keys."""
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
                results = PlexServiceCache._extract_metadata_items(container)
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
        except httpx.RequestError, ValueError:
            logger.exception("PlexService: search_show(%r) failed", title)
            return []

    async def _search_guid(
        self,
        guid: str,
        media_type: str,
    ) -> dict[str, Any] | None:
        """Search Plex by guid string and return first matching item."""
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
                results = PlexServiceCache._extract_metadata_items(container)
                for item in results:
                    if item.get("type") == media_type and item.get("ratingKey"):
                        normalized = PlexServiceCache._normalize_library_item(item)
                        if normalized is None:
                            continue
                        self._cache._cache_item(normalized, media_type=media_type)
                        return normalized
                return None
            return None
        except httpx.RequestError, ValueError:
            return None

    async def get_movie_by_tmdb(self, tmdb_id: int) -> dict[str, Any] | None:
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
        return await self._lookup_by_external_id(
            guid_type="tmdb",
            external_id=tmdb_id,
            media_type="movie",
        )

    async def check_movie_available(self, tmdb_id: int) -> bool:
        movie = await self.get_movie_by_tmdb(tmdb_id)
        if movie is None:
            return False
        return self._cache._is_available(movie)

    async def get_show_by_tmdb(self, tmdb_id: int) -> dict[str, Any] | None:
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
        return await self._lookup_by_external_id(
            guid_type="tmdb",
            external_id=tmdb_id,
            media_type="show",
        )

    async def get_show_by_tvdb(self, tvdb_id: int) -> dict[str, Any] | None:
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
        return await self._lookup_by_external_id(
            guid_type="tvdb",
            external_id=tvdb_id,
            media_type="show",
        )

    async def _get_metadata_children(
        self,
        rating_key: str,
        *,
        strict: bool,
    ) -> list[dict[str, Any]]:
        """Get metadata children with optional transient-failure propagation."""
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
                if strict:
                    raise PlexTransientScanError(
                        f"Plex metadata children lookup failed for {rating_key}: status={response.status_code}"
                    )
                logger.warning(
                    "PlexService: get_metadata_children(%s) returned status %d",
                    rating_key,
                    response.status_code,
                )
                return []

            data = response.json()
            container = data.get("MediaContainer", {})
            metadata = container.get("Metadata", [])
            return metadata if isinstance(metadata, list) else []
        except (httpx.RequestError, ValueError) as exc:
            if strict:
                raise PlexTransientScanError(
                    f"Plex metadata children lookup failed for {rating_key}: {exc}"
                ) from exc
            logger.exception("PlexService: get_metadata_children(%s) failed", rating_key)
            return []

    async def _get_metadata_children_strict(self, rating_key: str) -> list[dict[str, Any]]:
        """Get metadata children and raise on transient Plex failures."""
        return await self._get_metadata_children(rating_key, strict=True)

    async def _get_season_episodes_strict(
        self,
        season_infos: list[tuple[int, str]],
    ) -> list[list[dict[str, Any]]]:
        """Fetch season children with bounded concurrency and fail-fast cancellation."""
        if not season_infos:
            return []

        concurrency = max(1, self.settings.plex_sync_concurrency)
        results: list[list[dict[str, Any]] | None] = [None] * len(season_infos)
        season_iter = iter(enumerate(season_infos))
        in_flight: set[asyncio.Task[tuple[int, list[dict[str, Any]]]]] = set()

        async def fetch(index: int, season_rating_key: str) -> tuple[int, list[dict[str, Any]]]:
            return index, await self._get_metadata_children_strict(season_rating_key)

        def schedule_next() -> bool:
            try:
                index, (_, season_rating_key) = next(season_iter)
            except StopIteration:
                return False
            in_flight.add(asyncio.create_task(fetch(index, season_rating_key)))
            return True

        for _ in range(min(concurrency, len(season_infos))):
            schedule_next()

        try:
            while in_flight:
                done, pending = await asyncio.wait(
                    in_flight,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                in_flight = set(pending)
                for task in done:
                    index, episodes = await task
                    results[index] = episodes
                    schedule_next()
        except Exception:
            for task in in_flight:
                task.cancel()
            await asyncio.gather(*in_flight, return_exceptions=True)
            raise

        return [episodes if episodes is not None else [] for episodes in results]

    async def _get_episode_availability_result(
        self,
        rating_key: str,
    ) -> PlexEpisodeAvailabilityResult:
        """Get episode availability while preserving transient-failure semantics."""
        seasons = await self._get_metadata_children_strict(rating_key)
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
            if not isinstance(season_number, int):
                continue
            season_rating_key = season.get("ratingKey")
            if not season_rating_key:
                continue

            season_infos.append((season_number, str(season_rating_key)))

        season_episodes = await self._get_season_episodes_strict(season_infos)

        availability: dict[tuple[int, int], bool] = {}
        for (season_number, _), episodes in zip(season_infos, season_episodes, strict=True):
            available_in_season = 0
            for episode in episodes:
                if episode.get("type") != "episode":
                    continue
                episode_number = episode.get("index")
                if not isinstance(episode_number, int):
                    continue
                is_available = self._cache._is_available(episode)
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
        return PlexEpisodeAvailabilityResult(availability=availability, authoritative=True)

    async def get_episode_availability(self, rating_key: str) -> dict[tuple[int, int], bool]:
        """Get per-episode availability for a show."""
        try:
            result = await self._get_episode_availability_result(rating_key)
        except PlexTransientScanError:
            return {}
        return result.availability

    async def get_episode_availability_result(
        self, rating_key: str
    ) -> PlexEpisodeAvailabilityResult:
        """Get episode availability while preserving transient-failure semantics."""
        try:
            return await self._get_episode_availability_result(rating_key)
        except PlexTransientScanError:
            return PlexEpisodeAvailabilityResult(availability={}, authoritative=False)

    async def resolve_show_rating_key(
        self,
        tmdb_id: int | None = None,
        tvdb_id: int | None = None,
        title: str | None = None,
    ) -> tuple[str | None, bool]:
        """Resolve a show's Plex rating key via TMDB, TVDB, and title search fallback.

        Returns:
            (rating_key, authoritative) where authoritative is False when the
            Plex lookup was inconclusive (e.g. a section scan failed).
        """
        if tmdb_id:
            result = await self.lookup_show_by_tmdb(tmdb_id)
            if result.item:
                rk = result.item.get("rating_key")
                if rk:
                    return str(rk), True
            if not result.authoritative:
                return None, False

        if tvdb_id:
            result = await self.lookup_show_by_tvdb(tvdb_id)
            if result.item:
                rk = result.item.get("rating_key")
                if rk:
                    return str(rk), True
            if not result.authoritative:
                return None, False

        if title:
            results = await self.search_show(title)
            if results:
                rk = results[0].get("rating_key")
                if rk:
                    return str(rk), True

        return None, True
