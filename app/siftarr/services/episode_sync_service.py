"""Service for syncing TV episode data from Overseerr and Plex."""

import contextlib
import logging
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.siftarr.config import get_settings
from app.siftarr.models.episode import Episode
from app.siftarr.models.request import Request, RequestStatus
from app.siftarr.models.season import Season
from app.siftarr.services.async_utils import gather_limited
from app.siftarr.services.overseerr_service import OverseerrService
from app.siftarr.services.plex_service import PlexService

logger = logging.getLogger(__name__)


def _episode_needs_unreleased_status(air_date: date | None) -> bool:
    """Return whether an episode should be treated as unreleased."""
    return isinstance(air_date, date) and air_date > datetime.now(UTC).date()


def _derive_episode_status(*, is_on_plex: bool, air_date: date | None) -> RequestStatus:
    """Derive an episode status from Plex availability and air date."""
    if is_on_plex:
        return RequestStatus.COMPLETED
    if _episode_needs_unreleased_status(air_date):
        return RequestStatus.UNRELEASED
    return RequestStatus.PENDING


def _derive_season_status(episodes: list[Episode]) -> RequestStatus:
    """Derive the season status from episode statuses."""
    if not episodes:
        return RequestStatus.PENDING

    statuses = {episode.status for episode in episodes}
    if statuses == {RequestStatus.COMPLETED}:
        return RequestStatus.COMPLETED
    if statuses == {RequestStatus.UNRELEASED}:
        return RequestStatus.UNRELEASED
    if RequestStatus.COMPLETED in statuses:
        return RequestStatus.PENDING
    if RequestStatus.PENDING in statuses and RequestStatus.UNRELEASED in statuses:
        return RequestStatus.PENDING
    if RequestStatus.PENDING in statuses:
        return RequestStatus.PENDING
    return RequestStatus.PENDING


def _derive_request_status_from_seasons(seasons: list[Season]) -> RequestStatus:
    """Derive an aggregate TV request status from season statuses."""
    if not seasons:
        return RequestStatus.PENDING

    statuses = {season.status for season in seasons}
    if statuses == {RequestStatus.COMPLETED}:
        return RequestStatus.COMPLETED
    if statuses == {RequestStatus.UNRELEASED}:
        return RequestStatus.UNRELEASED
    if RequestStatus.COMPLETED in statuses or RequestStatus.PENDING in statuses:
        return RequestStatus.PENDING
    if RequestStatus.PENDING in statuses and RequestStatus.UNRELEASED in statuses:
        return RequestStatus.PENDING
    if RequestStatus.PENDING in statuses:
        return RequestStatus.PENDING
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
        self._stale_hours = get_settings().episode_sync_stale_hours

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

    async def _load_request_and_seasons(
        self, request_id: int
    ) -> tuple[Request | None, list[Season]]:
        """Load a request and all seasons with episodes in one round-trip."""
        request_result = await self.db.execute(select(Request).where(Request.id == request_id))
        request = request_result.scalar_one_or_none()
        if request is None:
            return None, []

        seasons_result = await self.db.execute(
            select(Season)
            .where(Season.request_id == request_id)
            .options(selectinload(Season.episodes))
            .order_by(Season.season_number)
        )
        return request, list(seasons_result.scalars().all())

    async def _apply_plex_to_existing_seasons(
        self,
        request: Request,
        seasons: list[Season],
    ) -> list[Season]:
        """Apply Plex availability to already persisted seasons without re-syncing Overseerr."""
        if self._plex is None or not seasons:
            return seasons
        return await self._apply_plex_availability(request, seasons)

    @staticmethod
    def _needs_plex_enrichment(request: Request, seasons: list[Season]) -> bool:
        """Detect fresh TV data that still needs episode-level Plex resolution."""
        if not seasons:
            return True

        if not getattr(request, "plex_rating_key", None):
            return True

        for season in seasons:
            episodes = list(getattr(season, "episodes", []) or [])
            if not episodes:
                return True
            episode_statuses = {episode.status for episode in episodes}
            if RequestStatus.PENDING in episode_statuses:
                return True

        return False

    async def _update_request_status(self, request: Request, seasons: list[Season]) -> None:
        """Persist aggregate TV request status from current season statuses."""
        request.status = _derive_request_status_from_seasons(seasons)
        await self.db.flush()

    async def _persist_episode_availability(
        self,
        request: Request,
        seasons: list[Season],
        availability: dict[tuple[int, int], bool],
    ) -> list[Season]:
        """Persist season/request aggregates from authoritative Plex availability."""
        for season in seasons:
            episodes = sorted(
                await self._load_season_episodes(season),
                key=lambda episode: episode.episode_number,
            )

            for episode in episodes:
                is_on_plex = availability.get((season.season_number, episode.episode_number), False)
                episode.status = _derive_episode_status(
                    is_on_plex=is_on_plex,
                    air_date=episode.air_date,
                )

            await self.db.flush()
            season.status = _derive_season_status(episodes)

        await self._update_request_status(request, seasons)
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
    ) -> Season | None:
        """Apply a single season payload to ORM rows serially."""
        season_number = season_info.get("seasonNumber", 0)
        if season_number == 0:
            return None

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

        # Disable autoflush while iterating episodes — previously dirty objects
        # (like the season row above) must not be flushed mid-query because
        # concurrent SQLite writers can cause "database is locked" errors.
        with self.db.no_autoflush:
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

        return season

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

        for season_info in seasons_data:
            episodes_data = self._get_season_episodes_payload(season_info, fetched_season_details)
            season = await self._upsert_season_from_overseerr(request, season_info, episodes_data)
            if season is not None:
                synced_seasons.append(season)

        await self._update_request_status(request, synced_seasons)

        return synced_seasons

    async def _resolve_plex_rating_key(self, request: Request) -> str | None:
        """Try to find and persist the Plex rating key for a request.

        Looks up the show by TMDB ID, then TVDB ID, then falls back to
        title search. Saves the result on the request so future syncs
        skip the lookup.
        """
        if self._plex is None:
            return None

        if request.plex_rating_key:
            return request.plex_rating_key

        rating_key: str | None = None

        if request.tmdb_id:
            result = await self._plex.get_show_by_tmdb(request.tmdb_id)
            if result:
                rating_key = str(result["rating_key"])
                logger.info(
                    "EpisodeSyncService: resolved Plex rating key via TMDB ID %s: %s",
                    request.tmdb_id,
                    rating_key,
                )

        if not rating_key and request.tvdb_id:
            result = await self._plex.get_show_by_tvdb(request.tvdb_id)
            if result:
                rating_key = str(result["rating_key"])
                logger.info(
                    "EpisodeSyncService: resolved Plex rating key via TVDB ID %s: %s",
                    request.tvdb_id,
                    rating_key,
                )

        if not rating_key and request.title:
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

        return rating_key

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
        """Override episode statuses based on Plex per-episode availability."""
        if self._plex is None:
            return seasons

        rating_key = await self._resolve_plex_rating_key(request)
        if not rating_key:
            logger.info(
                "EpisodeSyncService: could not resolve Plex rating key for request %s (%s), "
                "falling back to Overseerr-only statuses",
                request.id,
                request.title,
            )
            await self._apply_fallback_statuses(seasons)
            await self._apply_fallback_request_status(request, seasons)
            await self.db.commit()
            return seasons

        try:
            availability = await self._plex.get_episode_availability(rating_key)
            if not availability:
                logger.info(
                    "EpisodeSyncService: no episodes found on Plex for request %s (rating_key=%s)",
                    request.id,
                    rating_key,
                )
                await self._apply_fallback_statuses(seasons)
                await self._apply_fallback_request_status(request, seasons)
                await self.db.commit()
                return seasons

            await self._persist_episode_availability(request, seasons, availability)

            logger.info(
                "EpisodeSyncService: applied Plex availability for request %s (%d episodes on Plex)",
                request.id,
                sum(1 for v in availability.values() if v),
            )
        except Exception:
            logger.exception(
                "EpisodeSyncService: failed to apply Plex availability for request %s",
                request.id,
            )

        return seasons

    async def _apply_fallback_statuses(self, seasons: list[Season]) -> None:
        """When Plex data is unavailable, downgrade season-level statuses to per-episode defaults.

        Overseerr reports season-level statuses like 'partially_available'.  Applying that
        same status to every individual episode is semantically wrong — episodes should be
        either COMPLETED (on Plex) or PENDING (needs search).  When we can't reach Plex, we
        convert any non-standard episode statuses to PENDING so the UI and search logic work
        correctly.
        """
        for season in seasons:
            episodes = sorted(
                await self._load_season_episodes(season),
                key=lambda episode: episode.episode_number,
            )

            for episode in episodes:
                if episode.status == RequestStatus.PENDING or episode.status not in {
                    RequestStatus.COMPLETED,
                    RequestStatus.PENDING,
                    RequestStatus.UNRELEASED,
                }:
                    episode.status = (
                        RequestStatus.UNRELEASED
                        if _episode_needs_unreleased_status(episode.air_date)
                        else RequestStatus.PENDING
                    )

            season.status = _derive_season_status(episodes)

    async def _apply_fallback_request_status(self, request: Request, seasons: list[Season]) -> None:
        """Persist request aggregate when falling back to Overseerr-only episode state."""
        await self._update_request_status(request, seasons)

    async def sync_episodes(
        self, request_id: int, force_plex_refresh: bool = False
    ) -> list[Season]:
        """Fetch all seasons/episodes from Overseerr and upsert into Season/Episode tables.

        Args:
            request_id: The request ID to sync.
            force_plex_refresh: If True, always re-query Plex availability even if not stale.
        """
        result = await self.db.execute(select(Request).where(Request.id == request_id))
        request = result.scalar_one_or_none()
        if not request:
            logger.warning("EpisodeSyncService: request %s not found", request_id)
            return []

        if request.media_type.value != "tv":
            logger.warning("EpisodeSyncService: request %s is not TV type", request_id)
            return []

        synced_seasons = await self._sync_from_overseerr(request)

        if self._plex is not None:
            synced_seasons = await self._apply_plex_availability(request, synced_seasons)
        else:
            await self.db.commit()

        logger.info(
            "EpisodeSyncService: synced %d seasons for request %s",
            len(synced_seasons),
            request_id,
        )
        return synced_seasons

    async def refresh_if_stale(self, request_id: int) -> list[Season]:
        """Re-sync episodes if synced_at is older than the stale threshold.

        Also forces a re-sync when the request lacks a plex_rating_key and a
        PlexService is available, because that means per-episode Plex
        availability was never applied.
        """
        request, seasons = await self._load_request_and_seasons(request_id)
        if not request:
            return []

        if not seasons:
            return await self.sync_episodes(request_id)

        needs_plex_resolution = self._plex is not None and self._needs_plex_enrichment(
            request, seasons
        )

        newest_synced = max(
            (s.synced_at for s in seasons if s.synced_at),
            default=None,
        )

        if newest_synced is None:
            return await self.sync_episodes(request_id)

        stale_threshold = datetime.now(UTC) - timedelta(hours=self._stale_hours)
        # Ensure newest_synced is timezone-aware for comparison
        if newest_synced.tzinfo is None:
            newest_synced = newest_synced.replace(tzinfo=UTC)
        if newest_synced < stale_threshold:
            logger.info("EpisodeSyncService: stale sync for request %s, refreshing", request_id)
            return await self.sync_episodes(request_id)

        if needs_plex_resolution and self._plex is not None:
            logger.info(
                "EpisodeSyncService: request %s needs Plex enrichment, applying Plex to local data",
                request_id,
            )
            return await self._apply_plex_to_existing_seasons(request, seasons)

        return seasons
