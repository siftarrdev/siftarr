"""TV season/episode enrichment for dashboard detail responses."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from fastapi import BackgroundTasks
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.siftarr.models.request import RequestStatus
from app.siftarr.services.dashboard.dashboard_service import DashboardTVDetails
from app.siftarr.services.dashboard.tv_details_service import (
    compute_sync_metadata,
    count_request_episode_states,
    count_season_episode_states,
    load_tv_seasons_with_episodes,
)
from app.siftarr.services.releases.release_serializers import (
    apply_release_size_per_season_metadata,
    scope_to_episode_set,
)
from app.siftarr.services.utils.type_utils import coerce_int_list

logger = logging.getLogger(__name__)


class TVEnrichmentService:
    """Load and enrich TV season/episode data for dashboard details."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def known_total_seasons(self, request_id: int) -> int | None:
        """Return the count of known seasons for a TV request."""
        from app.siftarr.models.season import Season

        seasons_result = await self.db.execute(
            select(Season).where(Season.request_id == request_id).order_by(Season.season_number)
        )
        seasons = list(seasons_result.scalars().all())
        return len(seasons) or None

    async def load_tv_info(
        self,
        *,
        request_id: int,
        background_tasks: BackgroundTasks | None,
        releases: list[dict[str, object]],
        active_staged_torrents: list[dict[str, object]] | None = None,
    ) -> DashboardTVDetails:
        """Build TV detail payload with seasons, episodes, and release grouping."""
        active_staged_torrents = active_staged_torrents or []
        staged_overlay_torrents = [
            staged for staged in active_staged_torrents if staged.get("status") == "staged"
        ]
        seasons, episodes = await load_tv_seasons_with_episodes(self.db, request_id)

        episodes_by_season: dict[int, list[Any]] = {}
        for episode in episodes:
            episodes_by_season.setdefault(episode.season_id, []).append(episode)

        sync_state = compute_sync_metadata(
            seasons, episodes_by_season, request_id, background_tasks
        )
        seasons_data = []
        known_season_numbers: list[int] = []
        for season in seasons:
            known_season_numbers.append(season.season_number)
            season_episodes = episodes_by_season.get(season.id, [])
            available_count = sum(
                1 for ep in season_episodes if ep.status == RequestStatus.COMPLETED
            )
            state_counts = count_season_episode_states(season_episodes)
            season_staged = self._season_has_staged_scope(
                season.season_number, staged_overlay_torrents, known_season_numbers
            )
            staged_episode_numbers = {
                ep.episode_number
                for ep in season_episodes
                if self._episode_has_staged_scope(
                    season.season_number,
                    ep.episode_number,
                    staged_overlay_torrents,
                    known_season_numbers,
                )
            }
            staged_count = len(staged_episode_numbers)
            pending_count = max(state_counts["pending"] - staged_count, 0)
            seasons_data.append(
                {
                    "id": season.id,
                    "season_number": season.season_number,
                    "status": "staged" if season_staged else season.status.value,
                    "available_count": available_count,
                    "total_count": len(season_episodes),
                    "pending_count": pending_count,
                    "staged_count": staged_count,
                    "unreleased_count": state_counts["unreleased"],
                    "episodes": [
                        {
                            "id": ep.id,
                            "episode_number": ep.episode_number,
                            "title": ep.title,
                            "air_date": ep.air_date.isoformat() if ep.air_date else None,
                            "status": "staged"
                            if ep.episode_number in staged_episode_numbers
                            else ep.status.value,
                        }
                        for ep in season_episodes
                    ],
                }
            )

        self._apply_known_tv_release_metadata(releases, known_season_numbers)
        releases_by_season, releases_by_episode = self._group_tv_releases(
            releases, known_season_numbers
        )
        logger.info(
            "TV detail buckets loaded from DB releases: request_id=%s seasons=%s releases=%s season_buckets=%s episode_buckets=%s source=db",
            request_id,
            known_season_numbers,
            len(releases),
            len(releases_by_season),
            len(releases_by_episode),
        )
        return DashboardTVDetails(
            seasons=seasons_data,
            releases_by_season={str(k): v for k, v in releases_by_season.items()},
            releases_by_episode={f"{k[0]}-{k[1]}": v for k, v in releases_by_episode.items()},
            sync_state=sync_state,
            aggregate_counts=count_request_episode_states(seasons_data),
        )

    def _season_has_staged_scope(
        self,
        season_number: int,
        active_staged_torrents: list[dict[str, object]],
        known_season_numbers: list[int] | None = None,
    ) -> bool:
        for staged in active_staged_torrents:
            scope = staged.get("target_scope")
            if not isinstance(scope, Mapping):
                continue
            episode_set = scope_to_episode_set(scope, known_season_numbers)
            if not episode_set:
                return (
                    True  # Conservative: unresolved scope (e.g. complete_series) covers everything
                )
            if any(s == season_number for (s, e) in episode_set):
                return True
        return False

    def _episode_has_staged_scope(
        self,
        season_number: int,
        episode_number: int,
        active_staged_torrents: list[dict[str, object]],
        known_season_numbers: list[int] | None = None,
    ) -> bool:
        for staged in active_staged_torrents:
            scope = staged.get("target_scope")
            if not isinstance(scope, Mapping):
                continue
            episode_set = scope_to_episode_set(scope, known_season_numbers)
            if not episode_set:
                return True  # Conservative: unresolved scope covers everything
            # Match exact episode or full-season coverage (None wildcard)
            if (season_number, episode_number) in episode_set:
                return True
            if (season_number, None) in episode_set:
                return True
        return False

    def _apply_known_tv_release_metadata(
        self,
        releases: list[dict[str, object]],
        known_season_numbers: list[int],
    ) -> None:
        """Attach known-season metadata and per-season size to releases."""
        known_total_seasons = len(known_season_numbers)
        for release in releases:
            if "covered_seasons" not in release and not release.get("is_complete_series"):
                continue
            release["known_total_seasons"] = known_total_seasons
            covered_seasons = coerce_int_list(release.get("covered_seasons"))
            release["covers_all_known_seasons"] = bool(
                known_total_seasons
                and (
                    release.get("is_complete_series") or len(covered_seasons) >= known_total_seasons
                )
            )
            apply_release_size_per_season_metadata(release)

    def _group_tv_releases(
        self,
        releases: list[dict[str, object]],
        known_season_numbers: list[int],
    ) -> tuple[dict[int, list[dict[str, object]]], dict[tuple[int, int], list[dict[str, object]]]]:
        """Group releases by season and by episode for TV detail display."""
        releases_by_season: dict[int, list[dict[str, object]]] = {}
        releases_by_episode: dict[tuple[int, int], list[dict[str, object]]] = {}
        for release in releases:
            season_number = release.get("season_number")
            episode_number = release.get("episode_number")
            covered_seasons = coerce_int_list(release.get("covered_seasons"))
            if release.get("covers_all_known_seasons"):
                covered_seasons = known_season_numbers

            if isinstance(episode_number, int) and isinstance(season_number, int):
                releases_by_episode.setdefault((season_number, episode_number), []).append(release)
            elif covered_seasons:
                for covered_season in covered_seasons:
                    releases_by_season.setdefault(covered_season, []).append(release)
            elif isinstance(season_number, int):
                releases_by_season.setdefault(season_number, []).append(release)
        return releases_by_season, releases_by_episode
