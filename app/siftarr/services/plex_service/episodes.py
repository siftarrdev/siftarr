import asyncio
import logging
from typing import Any

import httpx

from .cache import PlexServiceCache
from .models import PlexEpisodeAvailabilityResult, PlexTransientScanError

logger = logging.getLogger(__name__)


class PlexServiceEpisodes:
    def __init__(self, service: Any, cache: PlexServiceCache) -> None:
        self._service = service
        self._cache = cache

    async def _get_metadata_children(
        self,
        rating_key: str,
        *,
        strict: bool,
    ) -> list[dict[str, Any]]:
        """Get metadata children with optional transient-failure propagation."""
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

    async def get_show_children(self, rating_key: str) -> list[dict[str, Any]]:
        """Get all seasons for a show."""
        return await self._get_metadata_children(rating_key, strict=False)

    async def get_season_children(self, rating_key: str) -> list[dict[str, Any]]:
        """Get all episodes for a season."""
        return await self._get_metadata_children(rating_key, strict=False)

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

        concurrency = max(1, self._service.settings.plex_sync_concurrency)
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
