"""Service for syncing TV episode data from Overseerr and Plex."""

import contextlib
import logging
from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.siftarr.config import get_settings
from app.siftarr.models.episode import Episode
from app.siftarr.models.request import Request, RequestStatus
from app.siftarr.models.season import Season
from app.siftarr.services.async_utils import gather_limited
from app.siftarr.services.overseerr_service import OverseerrService
from app.siftarr.services.plex_service import PlexLookupResult, PlexService

logger = logging.getLogger(__name__)


def _log(level: int, message: str, *args: object) -> None:
    """Log via the service logger, falling back to root if needed."""
    if not logger.disabled and logger.isEnabledFor(level) and (logger.handlers or logger.propagate):
        logger.log(level, message, *args)
        return

    logging.getLogger().log(level, message, *args)


def _episodes_are_unreleased(episodes: list[Episode]) -> bool:
    """Return whether any episode has a future air date."""
    today = datetime.now(UTC).date()
    return any(episode.air_date is not None and episode.air_date > today for episode in episodes)


def _derive_episode_status(*, is_on_plex: bool, air_date: date | None) -> RequestStatus:
    """Derive an episode status from Plex availability and air date."""
    if is_on_plex:
        return RequestStatus.COMPLETED

    if air_date is not None and air_date > datetime.now(UTC).date():
        return RequestStatus.UNRELEASED
    return RequestStatus.PENDING


def _derive_season_status(episodes: list[Episode]) -> RequestStatus:
    """Derive the season status from episode statuses."""
    if not episodes:
        return RequestStatus.PENDING

    statuses = {episode.status for episode in episodes}

    if statuses == {RequestStatus.COMPLETED}:
        return RequestStatus.COMPLETED
    if RequestStatus.COMPLETED in statuses:
        return RequestStatus.PENDING
    if RequestStatus.PENDING in statuses:
        return RequestStatus.PENDING
    if _episodes_are_unreleased(episodes):
        return RequestStatus.UNRELEASED
    return RequestStatus.PENDING


def _derive_request_status_from_episodes(episodes: list[Episode]) -> RequestStatus:
    """Derive an aggregate TV request status from episode statuses."""
    if not episodes:
        return RequestStatus.PENDING

    statuses = {episode.status for episode in episodes}

    if statuses == {RequestStatus.COMPLETED}:
        return RequestStatus.COMPLETED
    if RequestStatus.COMPLETED in statuses:
        return RequestStatus.PENDING
    if RequestStatus.PENDING in statuses:
        return RequestStatus.PENDING
    if _episodes_are_unreleased(episodes):
        return RequestStatus.UNRELEASED
    return RequestStatus.PENDING


def _derive_request_status_from_seasons(seasons: list[Season]) -> RequestStatus:
    """Compatibility wrapper that derives request status from season episodes."""
    episodes = [
        episode for season in seasons for episode in list(getattr(season, "episodes", []) or [])
    ]
    if episodes:
        return _derive_request_status_from_episodes(episodes)

    if not seasons:
        return RequestStatus.PENDING
    if all(season.status == RequestStatus.COMPLETED for season in seasons):
        return RequestStatus.COMPLETED
    if all(season.status == RequestStatus.UNRELEASED for season in seasons):
        return RequestStatus.UNRELEASED
    return RequestStatus.PENDING


class EpisodeSyncService:
    """Sync seasons and episodes from Overseerr into local DB, with per-episode Plex availability."""

    def __init__(
        self,
        db: AsyncSession,
        overseerr: OverseerrService | None = None,
        plex: PlexService | None = None,
    ):
        self.db = db
        self._overseerr = overseerr
        self._plex = plex

    @property
    def overseerr(self) -> OverseerrService:
        if self._overseerr is None:
            self._overseerr = OverseerrService(settings=get_settings())
        return self._overseerr

    @property
    def plex(self) -> PlexService | None:
        return self._plex

    def set_plex(self, plex: PlexService) -> None:
        """Set the Plex service instance."""
        self._plex = plex

    async def _update_request_status(self, request: Request, episodes: list[Episode]) -> None:
        """Persist aggregate TV request status from current episode statuses."""
        request.status = _derive_request_status_from_episodes(episodes)
        await self.db.flush()

    async def _persist_episode_availability(
        self,
        request: Request,
        seasons: list[Season],
        availability: dict[tuple[int, int], bool],
    ) -> list[Season]:
        """Persist season/request aggregates from authoritative Plex availability."""
        request_episodes: list[Episode] = []
        for season in seasons:
            episodes = sorted(
                await self._load_season_episodes(season),
                key=lambda episode: episode.episode_number,
            )
            request_episodes.extend(episodes)

            for episode in episodes:
                is_on_plex = availability.get((season.season_number, episode.episode_number), False)
                episode.status = _derive_episode_status(
                    is_on_plex=is_on_plex,
                    air_date=episode.air_date,
                )

            await self.db.flush()
            season.status = _derive_season_status(episodes)

        await self._update_request_status(request, request_episodes)
        await self.db.commit()
        return seasons

    async def reconcile_existing_seasons_from_plex(
        self,
        request: Request,
        seasons: list[Season],
        availability: dict[tuple[int, int], bool],
    ) -> list[Season]:
        """Recompute persisted TV availability from an authoritative Plex view."""
        if not seasons:
            return seasons
        return await self._persist_episode_availability(request, seasons, availability)

    @staticmethod
    def _get_season_episodes_payload(
        season_info: dict,
        fetched_season_details: dict[int, dict | None],
    ) -> list[dict]:
        """Return episode payloads from inline season data or fetched details."""
        inline_episodes = season_info.get("episodes")
        if inline_episodes is not None:
            return inline_episodes if isinstance(inline_episodes, list) else []

        season_number = season_info.get("seasonNumber", 0)
        season_detail = fetched_season_details.get(season_number)
        if not season_detail:
            return []

        episodes_data = season_detail.get("episodes", [])
        return episodes_data if isinstance(episodes_data, list) else []

    async def _collect_missing_season_details(
        self,
        request: Request,
        external_id: int,
        seasons_data: list[dict],
    ) -> dict[int, dict | None]:
        """Fetch missing season details with bounded concurrency."""
        missing_season_numbers = [
            season_info.get("seasonNumber", 0)
            for season_info in seasons_data
            if season_info.get("seasonNumber", 0) != 0 and season_info.get("episodes") is None
        ]
        if not missing_season_numbers:
            return {}

        async def fetch_one(season_number: int) -> tuple[int, dict | None]:
            try:
                season_detail = await self.overseerr.get_season_details(external_id, season_number)
            except Exception:
                logger.exception(
                    "EpisodeSyncService: failed to fetch season details for request %s season %s",
                    request.id,
                    season_number,
                )
                return season_number, None
            return season_number, season_detail

        results = await gather_limited(
            missing_season_numbers,
            get_settings().overseerr_sync_concurrency,
            fetch_one,
        )
        return dict(results)

    async def _upsert_season_from_overseerr(
        self,
        request: Request,
        season_info: dict,
        episodes_data: list[dict],
    ) -> tuple[Season | None, list[Episode]]:
        """Apply a single season payload to ORM rows serially."""
        season_number = season_info.get("seasonNumber", 0)
        if season_number == 0:
            return None, []

        season_result = await self.db.execute(
            select(Season).where(
                Season.request_id == request.id,
                Season.season_number == season_number,
            )
        )
        season = season_result.scalar_one_or_none()

        if season is None:
            season = Season(
                request_id=request.id,
                season_number=season_number,
                status=RequestStatus.PENDING,
                synced_at=datetime.now(UTC).replace(tzinfo=None),
            )
            self.db.add(season)
            await self.db.flush()
        else:
            season.synced_at = datetime.now(UTC).replace(tzinfo=None)
        season.status = RequestStatus.PENDING

        season_episodes: list[Episode] = []

        for episode_info in episodes_data:
            episode_number = episode_info.get("episodeNumber")
            if episode_number is None:
                continue

            ep_result = await self.db.execute(
                select(Episode).where(
                    Episode.season_id == season.id,
                    Episode.episode_number == episode_number,
                )
            )
            episode = ep_result.scalar_one_or_none()

            title = episode_info.get("title") or episode_info.get("name")
            air_date_str = episode_info.get("airDate") or episode_info.get("airDateUtc")
            air_date = None
            if air_date_str:
                with contextlib.suppress(ValueError, TypeError):
                    air_date = date.fromisoformat(air_date_str[:10])

            episode_status = _derive_episode_status(is_on_plex=False, air_date=air_date)

            if episode is None:
                episode = Episode(
                    season_id=season.id,
                    episode_number=episode_number,
                    title=title,
                    air_date=air_date,
                    status=episode_status,
                )
                self.db.add(episode)
            else:
                if title:
                    episode.title = title
                if air_date:
                    episode.air_date = air_date
                episode.status = episode_status

            season_episodes.append(episode)

        season.status = _derive_season_status(season_episodes)

        return season, season_episodes

    async def _sync_from_overseerr(self, request: Request) -> list[Season]:
        """Sync episode structure (titles, air dates) from Overseerr."""
        external_id = request.tmdb_id
        if not external_id:
            logger.warning(
                "EpisodeSyncService: request %s has no TMDB ID (required for Overseerr season API)",
                request.id,
            )
            return []

        media_type_for_api = "tv"
        media_details = await self.overseerr.get_media_details(media_type_for_api, external_id)
        if not media_details:
            logger.warning(
                "EpisodeSyncService: no media details for request %s (external_id=%s)",
                request.id,
                external_id,
            )
            return []

        seasons_data = media_details.get("seasons", [])
        if not seasons_data:
            logger.info(
                "EpisodeSyncService: no seasons in media details for request %s", request.id
            )
            return []

        fetched_season_details = await self._collect_missing_season_details(
            request,
            external_id,
            seasons_data,
        )
        synced_seasons: list[Season] = []
        synced_episodes: list[Episode] = []

        for season_info in seasons_data:
            episodes_data = self._get_season_episodes_payload(season_info, fetched_season_details)
            season, season_episodes = await self._upsert_season_from_overseerr(
                request,
                season_info,
                episodes_data,
            )
            if season is not None:
                synced_seasons.append(season)
                synced_episodes.extend(season_episodes)

        await self._update_request_status(request, synced_episodes)
        await self.db.commit()

        return synced_seasons

    async def _resolve_plex_rating_key(self, request: Request) -> tuple[str | None, bool]:
        """Try to find and persist the Plex rating key for a request.

        Looks up the show by TMDB ID, then TVDB ID, then falls back to
        title search. Saves the result on the request so future syncs
        skip the lookup.
        """
        if self._plex is None:
            return None, True

        if request.plex_rating_key:
            return request.plex_rating_key, True

        rating_key: str | None = None
        authoritative = True

        def resolve_lookup(result: PlexLookupResult | None) -> str | None:
            if result is None:
                return None
            if result.item is None:
                return None
            raw_rating_key = result.item.get("rating_key")
            return str(raw_rating_key) if raw_rating_key else None

        if request.tmdb_id:
            result = await self._plex.lookup_show_by_tmdb(request.tmdb_id)
            rating_key = resolve_lookup(result)
            authoritative = authoritative and result.authoritative
            if not result.authoritative and not rating_key:
                return None, False
            if rating_key:
                logger.info(
                    "EpisodeSyncService: resolved Plex rating key via TMDB ID %s: %s",
                    request.tmdb_id,
                    rating_key,
                )

        if not rating_key and request.tvdb_id:
            result = await self._plex.lookup_show_by_tvdb(request.tvdb_id)
            rating_key = resolve_lookup(result)
            authoritative = authoritative and result.authoritative
            if not result.authoritative and not rating_key:
                return None, False
            if rating_key:
                logger.info(
                    "EpisodeSyncService: resolved Plex rating key via TVDB ID %s: %s",
                    request.tvdb_id,
                    rating_key,
                )

        if not rating_key and authoritative and request.title:
            results = await self._plex.search_show(request.title)
            if results:
                rating_key = str(results[0]["rating_key"])
                logger.info(
                    "EpisodeSyncService: resolved Plex rating key via title search (%s): %s",
                    request.title,
                    rating_key,
                )

        if rating_key:
            request.plex_rating_key = rating_key
            await self.db.flush()
            logger.info(
                "EpisodeSyncService: saved Plex rating key %s for request %s (%s)",
                rating_key,
                request.id,
                request.title,
            )
        else:
            # Not a warning - show simply doesn't exist in Plex yet (expected for new requests)
            logger.debug(
                "EpisodeSyncService: could not resolve Plex rating key for request %s "
                "(tmdb_id=%s, tvdb_id=%s, title=%s) - show not in Plex yet",
                request.id,
                request.tmdb_id,
                request.tvdb_id,
                request.title,
            )

        return rating_key, authoritative

    async def _load_season_episodes(self, season: Season) -> list[Episode]:
        """Load episodes for a season via explicit async query (avoids lazy-load in async context)."""
        loaded_episodes = getattr(season, "__dict__", {}).get("episodes")
        if loaded_episodes is not None:
            return list(loaded_episodes)

        episodes_result = await self.db.execute(
            select(Episode).where(Episode.season_id == season.id)
        )
        return list(episodes_result.scalars().all())

    async def _apply_plex_availability(
        self, request: Request, seasons: list[Season]
    ) -> list[Season]:
        """Override episode statuses based on authoritative Plex availability."""
        if self._plex is None or not seasons:
            return seasons

        rating_key, lookup_authoritative = await self._resolve_plex_rating_key(request)
        if not lookup_authoritative:
            _log(
                logging.WARNING,
                "EpisodeSyncService: degraded Plex sync for request %s (%s); "
                "Plex lookup was inconclusive, preserving existing episode/request state",
                request.id,
                request.title,
            )
            return seasons

        if not rating_key:
            logger.info(
                "EpisodeSyncService: could not resolve Plex rating key for request %s (%s)",
                request.id,
                request.title,
            )
            return seasons

        availability_result = await self._plex.get_episode_availability_result(rating_key)
        if not availability_result.authoritative:
            _log(
                logging.WARNING,
                "EpisodeSyncService: degraded Plex sync for request %s (%s); "
                "Plex episode availability was inconclusive, preserving existing episode/request state",
                request.id,
                request.title,
            )
            return seasons

        await self._persist_episode_availability(request, seasons, availability_result.availability)

        logger.info(
            "EpisodeSyncService: applied Plex availability for request %s (%d episodes on Plex)",
            request.id,
            sum(1 for v in availability_result.availability.values() if v),
        )

        return seasons

    async def sync_request(self, request_id: int) -> list[Season]:
        """Sync TV metadata from Overseerr, then apply Plex episode availability."""
        result = await self.db.execute(select(Request).where(Request.id == request_id))
        request = result.scalar_one_or_none()
        if not request:
            logger.warning("EpisodeSyncService: request %s not found", request_id)
            return []

        if request.media_type.value != "tv":
            logger.warning("EpisodeSyncService: request %s is not TV type", request_id)
            return []

        synced_seasons = await self._sync_from_overseerr(request)

        if self._plex is None:
            logger.info(
                "EpisodeSyncService: synced %d seasons for request %s",
                len(synced_seasons),
                request_id,
            )
            return synced_seasons

        try:
            synced_seasons = await self._apply_plex_availability(request, synced_seasons)
        except Exception:
            logger.exception(
                "EpisodeSyncService: failed to apply Plex availability for request %s",
                request.id,
            )
            await self.db.commit()

        logger.info(
            "EpisodeSyncService: synced %d seasons for request %s",
            len(synced_seasons),
            request_id,
        )
        return synced_seasons
