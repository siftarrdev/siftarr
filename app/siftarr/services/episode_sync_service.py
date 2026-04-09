"""Service for syncing TV episode data from Overseerr."""

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

logger = logging.getLogger(__name__)


class EpisodeSyncService:
    """Sync seasons and episodes from Overseerr into local DB."""

    def __init__(self, db: AsyncSession, overseerr: OverseerrService | None = None):
        self.db = db
        self._overseerr = overseerr
        self._stale_hours = get_settings().episode_sync_stale_hours

    @property
    def overseerr(self) -> OverseerrService:
        if self._overseerr is None:
            self._overseerr = OverseerrService(settings=get_settings())
        return self._overseerr

    async def sync_episodes(self, request_id: int) -> list[Season]:
        """Fetch all seasons/episodes from Overseerr and upsert into Season/Episode tables."""
        result = await self.db.execute(select(Request).where(Request.id == request_id))
        request = result.scalar_one_or_none()
        if not request:
            logger.warning("EpisodeSyncService: request %s not found", request_id)
            return []

        if request.media_type.value != "tv":
            logger.warning("EpisodeSyncService: request %s is not TV type", request_id)
            return []

        external_id = request.tmdb_id
        if not external_id:
            logger.warning(
                "EpisodeSyncService: request %s has no TMDB ID (required for Overseerr season API)",
                request_id,
            )
            return []

        media_type_for_api = "tv"
        media_details = await self.overseerr.get_media_details(media_type_for_api, external_id)
        if not media_details:
            logger.warning(
                "EpisodeSyncService: no media details for request %s (external_id=%s)",
                request_id,
                external_id,
            )
            return []

        seasons_data = media_details.get("seasons", [])
        if not seasons_data:
            logger.info(
                "EpisodeSyncService: no seasons in media details for request %s", request_id
            )
            return []

        synced_seasons: list[Season] = []

        for season_info in seasons_data:
            season_number = season_info.get("seasonNumber", 0)
            if season_number == 0:
                continue

            season_result = await self.db.execute(
                select(Season).where(
                    Season.request_id == request_id,
                    Season.season_number == season_number,
                )
            )
            season = season_result.scalar_one_or_none()

            if season is None:
                season = Season(
                    request_id=request_id,
                    season_number=season_number,
                    status=RequestStatus.RECEIVED,
                    synced_at=datetime.now(UTC).replace(tzinfo=None),
                )
                self.db.add(season)
                await self.db.flush()
            else:
                season.synced_at = datetime.now(UTC).replace(tzinfo=None)

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

                if episode is None:
                    episode = Episode(
                        season_id=season.id,
                        episode_number=episode_number,
                        title=title,
                        air_date=air_date,
                        status=RequestStatus.RECEIVED,
                    )
                    self.db.add(episode)
                else:
                    if title:
                        episode.title = title
                    if air_date:
                        episode.air_date = air_date

        await self.db.commit()

        logger.info(
            "EpisodeSyncService: synced %d seasons for request %s",
            len(synced_seasons),
            request_id,
        )
        return synced_seasons

    async def refresh_if_stale(self, request_id: int) -> list[Season]:
        """Re-sync episodes if synced_at is older than the stale threshold."""
        result = await self.db.execute(select(Season).where(Season.request_id == request_id))
        seasons = list(result.scalars().all())

        if not seasons:
            return await self.sync_episodes(request_id)

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

        return seasons
