"""Service for fetching per-episode availability from Plex."""

import logging
from typing import Any

import httpx

from app.siftarr.config import Settings, get_settings
from app.siftarr.services.http_client import get_shared_client

logger = logging.getLogger(__name__)


class PlexService:
    """Service for fetching per-episode availability from Plex."""

    def __init__(self, settings: Settings | None = None) -> None:
        """Initialize the Plex service."""
        self.settings = settings or get_settings()
        self.base_url = str(self.settings.plex_url).rstrip("/") if self.settings.plex_url else None
        self.token = self.settings.plex_token

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
                results = container.get("Metadata", [])
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

    async def get_show_by_tmdb(self, tmdb_id: int) -> dict[str, Any] | None:
        """Find a show in Plex library by TMDB ID using Plex's guid system.

        Args:
            tmdb_id: The TMDB ID to search for.

        Returns:
            Show metadata dict if found, None otherwise.
        """
        if not self.base_url or not self.token:
            return None

        endpoint = f"{self.base_url}/library/search"
        client = await self._get_client()
        guid = f"com.plexapp.agents.themoviedb://{tmdb_id}"
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
                results = container.get("Metadata", [])
                for item in results:
                    if item.get("type") == "show" and item.get("ratingKey"):
                        logger.info(
                            "PlexService: get_show_by_tmdb(%s) found rating_key=%s",
                            tmdb_id,
                            item.get("ratingKey"),
                        )
                        return {
                            "rating_key": item.get("ratingKey"),
                            "title": item.get("title"),
                            "year": item.get("year"),
                            "guid": item.get("guid"),
                        }
                logger.info("PlexService: get_show_by_tmdb(%s) found no show match", tmdb_id)
            else:
                logger.warning(
                    "PlexService: get_show_by_tmdb(%s) returned status %d",
                    tmdb_id,
                    response.status_code,
                )
            return None
        except (httpx.RequestError, ValueError):
            logger.exception("PlexService: get_show_by_tmdb(%s) failed", tmdb_id)
            return None

    async def get_show_by_tvdb(self, tvdb_id: int) -> dict[str, Any] | None:
        """Find a show in Plex library by TVDB ID using Plex's guid system.

        Args:
            tvdb_id: The TVDB ID to search for.

        Returns:
            Show metadata dict if found, None otherwise.
        """
        if not self.base_url or not self.token:
            return None

        endpoint = f"{self.base_url}/library/search"
        client = await self._get_client()
        guid = f"com.plexapp.agents.thetvdb://{tvdb_id}"
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
                results = container.get("Metadata", [])
                for item in results:
                    if item.get("type") == "show" and item.get("ratingKey"):
                        logger.info(
                            "PlexService: get_show_by_tvdb(%s) found rating_key=%s",
                            tvdb_id,
                            item.get("ratingKey"),
                        )
                        return {
                            "rating_key": item.get("ratingKey"),
                            "title": item.get("title"),
                            "year": item.get("year"),
                            "guid": item.get("guid"),
                        }
                logger.info("PlexService: get_show_by_tvdb(%s) found no show match", tvdb_id)
            else:
                logger.warning(
                    "PlexService: get_show_by_tvdb(%s) returned status %d",
                    tvdb_id,
                    response.status_code,
                )
            return None
        except (httpx.RequestError, ValueError):
            logger.exception("PlexService: get_show_by_tvdb(%s) failed", tvdb_id)
            return None

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

    async def get_episode_availability(self, rating_key: str) -> dict[tuple[int, int], bool]:
        """Get per-episode availability for a show.

        Queries all seasons and episodes to build a map of which episodes
        are available on Plex.

        Args:
            rating_key: The Plex rating key for the show.

        Returns:
            Dict mapping (season_number, episode_number) -> available (True/False).
        """
        availability: dict[tuple[int, int], bool] = {}

        seasons = await self.get_show_children(rating_key)
        logger.info(
            "PlexService: get_episode_availability(rating_key=%s) found %d season(s)",
            rating_key,
            len(seasons),
        )
        for season in seasons:
            if season.get("type") != "season":
                continue
            season_number = season.get("index")
            if season_number is None:
                continue
            season_rating_key = season.get("ratingKey")
            if not season_rating_key:
                continue

            episodes = await self.get_season_children(season_rating_key)
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

        sections = await self._get_tv_library_sections()
        rating_keys: list[str] = []

        for section_key in sections:
            keys = await self._get_section_shows(section_key)
            rating_keys.extend(keys)

        return rating_keys

    async def _get_tv_library_sections(self) -> list[str]:
        """Get the library section keys for TV content."""
        if not self.base_url or not self.token:
            return []

        endpoint = f"{self.base_url}/library/sections"
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
                sections = container.get("Directory", [])
                return [
                    str(s.get("key")) for s in sections if s.get("type") == "show" and s.get("key")
                ]
            return []
        except (httpx.RequestError, ValueError):
            return []

    async def _get_section_shows(self, section_key: str) -> list[str]:
        """Get all show rating keys in a library section."""
        if not self.base_url or not self.token:
            return []

        endpoint = f"{self.base_url}/library/sections/{section_key}/all"
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
                metadata = container.get("Metadata", [])
                return [str(m.get("ratingKey", "")) for m in metadata if m.get("ratingKey")]
            return []
        except (httpx.RequestError, ValueError):
            return []
