import logging
from typing import Any

import httpx

from app.siftarr.services.async_utils import gather_limited

from .cache import PlexServiceCache
from .models import PlexEpisodeAvailabilityResult, PlexTransientScanError

logger = logging.getLogger(__name__)


class PlexServiceEpisodes:
    def __init__(self, service: Any, cache: PlexServiceCache) -> None:
        self._service = service
        self._cache = cache

    async def get_show_children(self, rating_key: str) -> list[dict[str, Any]]:
        """Get all seasons for a show."""
        if not self._service.base_url or not self._service.token:
            return []

        endpoint = f"{self._service.base_url}/library/metadata/{rating_key}/children"
        client = await self._service._get_client()

        try:
            response = await client.get(
                endpoint,
                headers=self._service._get_headers(),
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
        """Get all episodes for a season."""
        if not self._service.base_url or not self._service.token:
            return []

        endpoint = f"{self._service.base_url}/library/metadata/{rating_key}/children"
        client = await self._service._get_client()

        try:
            response = await client.get(
                endpoint,
                headers=self._service._get_headers(),
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
        if not self._service.base_url or not self._service.token:
            return []

        endpoint = f"{self._service.base_url}/library/metadata/{rating_key}/children"
        client = await self._service._get_client()

        try:
            response = await client.get(
                endpoint,
                headers=self._service._get_headers(),
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
        """Get per-episode availability for a show."""
        seasons = await self._service.get_show_children(rating_key)
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

        season_episodes = await gather_limited(
            (season_rating_key for _, season_rating_key in season_infos),
            self._service.settings.plex_sync_concurrency,
            self._service.get_season_children,
        )

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
        return availability

    async def get_episode_availability_result(
        self, rating_key: str
    ) -> PlexEpisodeAvailabilityResult:
        """Get episode availability while preserving transient-failure semantics."""
        try:
            seasons = await self._service._get_metadata_children_strict(rating_key)
        except PlexTransientScanError:
            return PlexEpisodeAvailabilityResult(availability={}, authoritative=False)

        season_infos: list[tuple[int, str]] = []
        for season in seasons:
            if season.get("type") != "season":
                continue
            season_number = season.get("index")
            season_rating_key = season.get("ratingKey")
            if not isinstance(season_number, int) or not season_rating_key:
                continue
            season_infos.append((season_number, str(season_rating_key)))

        availability: dict[tuple[int, int], bool] = {}
        for season_number, season_rating_key in season_infos:
            try:
                episodes = await self._service._get_metadata_children_strict(season_rating_key)
            except PlexTransientScanError:
                return PlexEpisodeAvailabilityResult(availability={}, authoritative=False)

            for episode in episodes:
                if episode.get("type") != "episode":
                    continue
                episode_number = episode.get("index")
                if not isinstance(episode_number, int):
                    continue
                availability[(season_number, episode_number)] = self._cache._is_available(episode)

        return PlexEpisodeAvailabilityResult(availability=availability, authoritative=True)
