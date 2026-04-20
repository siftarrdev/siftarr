from collections.abc import AsyncIterator
from typing import Any

import httpx

from .models import PlexLibraryScanResult, PlexTransientScanError


class PlexServiceLibraryScanMixin:
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
