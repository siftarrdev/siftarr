"""Service for syncing TV episode data from Overseerr and Plex."""

import contextlib
import logging
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.siftarr.config import get_settings
from app.siftarr.models.episode import Episode
from app.siftarr.models.request import Request, RequestStatus
from app.siftarr.models.season import Season
from app.siftarr.services.overseerr_service import OverseerrService
from app.siftarr.services.plex_service import PlexService

logger = logging.getLogger(__name__)

OVERSEERR_MEDIA_STATUS_MAP = {
    1: "unknown",
    2: "pending",
    3: "processing",
    4: "partially_available",
    5: "available",
    6: "deleted",
}


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

    def _overseerr_status_to_request_status(self, status: int | None) -> RequestStatus:
        if status is None:
            return RequestStatus.RECEIVED
        status_str = OVERSEERR_MEDIA_STATUS_MAP.get(status, "unknown")
        if status_str == "available":
            return RequestStatus.AVAILABLE
        if status_str == "partially_available":
            return RequestStatus.PARTIALLY_AVAILABLE
        if status_str in ("pending", "processing", "unknown"):
            return RequestStatus.PENDING
        return RequestStatus.RECEIVED

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

        media_info = media_details.get("mediaInfo", {})
        season_statuses: dict[int, int] = {}
        for s in media_info.get("seasons", []):
            season_statuses[s.get("seasonNumber", 0)] = s.get("status", 0)

        if not season_statuses and request.overseerr_request_id:
            overseerr_request = await self.overseerr.get_request(request.overseerr_request_id)
            if overseerr_request:
                for s in overseerr_request.get("seasons", []):
                    season_statuses[s.get("seasonNumber", 0)] = s.get("status", 0)

        seasons_data = media_details.get("seasons", [])
        if not seasons_data:
            logger.info(
                "EpisodeSyncService: no seasons in media details for request %s", request.id
            )
            return []

        synced_seasons: list[Season] = []

        for season_info in seasons_data:
            season_number = season_info.get("seasonNumber", 0)
            if season_number == 0:
                continue

            season_result = await self.db.execute(
                select(Season).where(
                    Season.request_id == request.id,
                    Season.season_number == season_number,
                )
            )
            season = season_result.scalar_one_or_none()

            overseerr_status = season_statuses.get(season_number)
            overseerr_season_status = self._overseerr_status_to_request_status(overseerr_status)

            if season is None:
                season = Season(
                    request_id=request.id,
                    season_number=season_number,
                    status=overseerr_season_status,
                    synced_at=datetime.now(UTC).replace(tzinfo=None),
                )
                self.db.add(season)
                await self.db.flush()
            else:
                season.synced_at = datetime.now(UTC).replace(tzinfo=None)
                season.status = overseerr_season_status

            synced_seasons.append(season)

            episodes_data = season_info.get("episodes", [])
            if not episodes_data:
                season_detail = await self.overseerr.get_season_details(external_id, season_number)
                if season_detail:
                    episodes_data = season_detail.get("episodes", [])

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

                episode_status = overseerr_season_status

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
            logger.warning(
                "EpisodeSyncService: could not resolve Plex rating key for request %s "
                "(tmdb_id=%s, tvdb_id=%s, title=%s)",
                request.id,
                request.tmdb_id,
                request.tvdb_id,
                request.title,
            )

        return rating_key

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
                await self.db.commit()
                return seasons

            for season in seasons:
                episodes_result = await self.db.execute(
                    select(Episode).where(Episode.season_id == season.id)
                )
                episodes = list(episodes_result.scalars().all())

                for episode in episodes:
                    is_on_plex = availability.get(
                        (season.season_number, episode.episode_number), False
                    )
                    if is_on_plex:
                        episode.status = RequestStatus.AVAILABLE
                    else:
                        episode.status = RequestStatus.PENDING

                await self.db.flush()

                available_count = sum(1 for ep in episodes if ep.status == RequestStatus.AVAILABLE)
                if available_count == len(episodes) and len(episodes) > 0:
                    season.status = RequestStatus.AVAILABLE
                elif available_count > 0:
                    season.status = RequestStatus.PARTIALLY_AVAILABLE
                else:
                    season.status = RequestStatus.PENDING

            await self.db.commit()

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
        either AVAILABLE (on Plex) or PENDING (needs search).  When we can't reach Plex, we
        convert PARTIALLY_AVAILABLE episodes to PENDING so the UI and search logic work
        correctly.
        """
        for season in seasons:
            if season.status != RequestStatus.PARTIALLY_AVAILABLE:
                continue
            episodes_result = await self.db.execute(
                select(Episode).where(Episode.season_id == season.id)
            )
            episodes = list(episodes_result.scalars().all())
            for episode in episodes:
                if episode.status == RequestStatus.PARTIALLY_AVAILABLE:
                    episode.status = RequestStatus.PENDING
            available = sum(1 for ep in episodes if ep.status == RequestStatus.AVAILABLE)
            total = len(episodes)
            if available == 0 and total > 0:
                season.status = RequestStatus.PENDING

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
        request_result = await self.db.execute(select(Request).where(Request.id == request_id))
        request = request_result.scalar_one_or_none()
        if not request:
            return []

        result = await self.db.execute(select(Season).where(Season.request_id == request_id))
        seasons = list(result.scalars().all())

        if not seasons:
            return await self.sync_episodes(request_id)

        needs_plex_resolution = self._plex is not None and not request.plex_rating_key

        newest_synced = max(
            (s.synced_at for s in seasons if s.synced_at),
            default=None,
        )

        if newest_synced is None:
            return await self.sync_episodes(request_id)

        stale_threshold = datetime.now(UTC).replace(tzinfo=None) - timedelta(
            hours=self._stale_hours
        )
        if newest_synced < stale_threshold:
            logger.info("EpisodeSyncService: stale sync for request %s, refreshing", request_id)
            return await self.sync_episodes(request_id)

        if needs_plex_resolution and self._plex is not None:
            logger.info(
                "EpisodeSyncService: request %s has no plex_rating_key, forcing re-sync",
                request_id,
            )
            return await self.sync_episodes(request_id)

        return seasons
