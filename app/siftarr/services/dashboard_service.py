"""Dashboard data loading for request details and search endpoints."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any

from fastapi import BackgroundTasks
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.siftarr.config import Settings, get_settings
from app.siftarr.models.request import MediaType, RequestStatus, is_active_staging_workflow_status
from app.siftarr.models.rule import Rule
from app.siftarr.models.staged_torrent import StagedTorrent
from app.siftarr.services.activity_log_service import ActivityLogService
from app.siftarr.services.overseerr_service import (
    OverseerrService,
    build_overseerr_media_url,
    build_poster_url,
)
from app.siftarr.services.prowlarr_service import ProwlarrRelease, ProwlarrService
from app.siftarr.services.release_parser import (
    is_exact_single_episode_release,
    parse_release_coverage,
)
from app.siftarr.services.release_serializers import (
    apply_active_selection_metadata,
    apply_release_size_per_season_metadata,
    finalize_releases,
    season_pack_release_sort_key,
    serialize_active_staged_torrent,
    serialize_evaluated_release,
    serialize_stored_evaluated_release,
)
from app.siftarr.services.release_storage import build_prowlarr_release
from app.siftarr.services.request_service import ensure_tvdb_id
from app.siftarr.services.rule_engine import RuleEngine
from app.siftarr.services.tv_details_service import (
    compute_sync_metadata,
    count_request_episode_states,
    count_season_episode_states,
    load_tv_seasons_with_episodes,
)
from app.siftarr.services.type_utils import coerce_int_list


@dataclass(slots=True)
class DashboardRequestSummary:
    id: int
    title: str
    status: str
    media_type: str


@dataclass(slots=True)
class DashboardOverseerrDetails:
    overview: str
    poster: str | None
    status: str
    url: str | None


@dataclass(slots=True)
class DashboardTVDetails:
    seasons: list[dict[str, object]]
    releases_by_season: dict[str, list[dict[str, object]]]
    releases_by_episode: dict[str, list[dict[str, object]]]
    sync_state: dict[str, object]
    aggregate_counts: dict[str, int]


@dataclass(slots=True)
class DashboardTimelineEntry:
    id: int
    event_type: str
    details: object | None
    created_at: str | None


@dataclass(slots=True)
class RequestDetailsData:
    request: DashboardRequestSummary
    releases: list[dict[str, object]]
    active_staged_torrent: dict[str, object] | None
    active_staged_torrents: list[dict[str, object]]
    overseerr: DashboardOverseerrDetails | None
    tv_info: DashboardTVDetails | None
    timeline: list[DashboardTimelineEntry]


@dataclass(slots=True)
class RequestSearchData:
    request: DashboardRequestSummary
    releases: list[dict[str, object]]


@dataclass(slots=True)
class TVSearchData:
    releases: list[dict[str, object]]
    known_total_seasons: int | None = None
    error: str | None = None


class DashboardService:
    """Load dashboard response data while keeping routers thin."""

    def __init__(self, db: AsyncSession, *, settings: Settings | None = None) -> None:
        self.db = db
        self.settings = settings or get_settings()

    async def load_request_details(
        self,
        request: Any,
        *,
        request_id: int,
        background_tasks: BackgroundTasks,
    ) -> RequestDetailsData:
        releases = await self._load_serialized_stored_releases(request_id, media_type=request.media_type)
        active_staged_torrents = await self._load_active_staged_payloads(
            request_id,
            media_type=request.media_type,
            request_status=request.status,
        )
        apply_active_selection_metadata(releases, active_staged_torrents, media_type=request.media_type)

        tv_info = None
        if request.media_type == MediaType.TV:
            tv_info = await self._load_tv_info(
                request_id=request_id,
                background_tasks=background_tasks,
                releases=releases,
            )

        return RequestDetailsData(
            request=DashboardRequestSummary(
                id=request.id,
                title=request.title,
                status=request.status.value,
                media_type=request.media_type.value,
            ),
            releases=releases,
            active_staged_torrent=active_staged_torrents[0] if active_staged_torrents else None,
            active_staged_torrents=active_staged_torrents,
            overseerr=await self._load_overseerr_details(request),
            tv_info=tv_info,
            timeline=await self._load_timeline(request_id),
        )

    async def load_movie_search_results(self, request: Any, *, request_id: int) -> RequestSearchData:
        return RequestSearchData(
            request=DashboardRequestSummary(
                id=request.id,
                title=request.title,
                status=request.status.value,
                media_type=request.media_type.value,
            ),
            releases=await self._load_serialized_stored_releases(request_id, media_type=request.media_type),
        )

    async def search_season_packs(self, request: Any, *, season_number: int) -> TVSearchData:
        result = await self._search_tv(request, season=season_number)
        if result.error:
            return TVSearchData(releases=[], error=result.error)

        engine = await self._build_rule_engine(media_type="tv")
        releases = []
        for release in result.releases:
            coverage = parse_release_coverage(release.title)
            if coverage.episode_number is not None:
                continue
            if coverage.is_complete_series:
                continue
            if coverage.season_numbers != (season_number,):
                continue
            releases.append(
                serialize_evaluated_release(release, engine.evaluate(release), coverage=coverage)
            )
        return TVSearchData(
            releases=finalize_releases(releases, sort_key=season_pack_release_sort_key)
        )

    async def search_all_season_packs(self, request: Any, *, request_id: int) -> TVSearchData:
        known_total_seasons = await self._known_total_seasons(request_id)
        result = await self._search_tv(request)
        if result.error:
            return TVSearchData(releases=[], known_total_seasons=known_total_seasons, error=result.error)

        engine = await self._build_rule_engine(media_type="tv")
        releases = []
        for release in result.releases:
            coverage = parse_release_coverage(release.title)
            if coverage.episode_number is not None:
                continue
            if not coverage.is_complete_series and len(coverage.season_numbers) <= 1:
                continue
            releases.append(
                serialize_evaluated_release(
                    release,
                    engine.evaluate(release),
                    coverage=coverage,
                    known_total_seasons=known_total_seasons,
                )
            )
        return TVSearchData(
            releases=finalize_releases(releases, sort_key=season_pack_release_sort_key),
            known_total_seasons=known_total_seasons,
        )

    async def search_episode(
        self,
        request: Any,
        *,
        season_number: int,
        episode_number: int,
    ) -> TVSearchData:
        result = await self._search_tv(request, season=season_number, episode=episode_number)
        if result.error:
            return TVSearchData(releases=[], error=result.error)

        engine = await self._build_rule_engine(media_type="tv")
        releases = []
        for release in result.releases:
            coverage = parse_release_coverage(release.title)
            if coverage.is_complete_series:
                continue
            if coverage.season_numbers != (season_number,):
                continue
            if coverage.episode_number != episode_number:
                continue
            if not is_exact_single_episode_release(release.title, season_number, episode_number):
                continue
            releases.append(serialize_evaluated_release(release, engine.evaluate(release)))
        return TVSearchData(releases=finalize_releases(releases))

    async def _load_overseerr_details(self, request: Any) -> DashboardOverseerrDetails | None:
        if not request.overseerr_request_id:
            return None

        overseerr_service = OverseerrService(settings=self.settings)
        try:
            ov_task = asyncio.create_task(overseerr_service.get_request(request.overseerr_request_id))
            media_details_task = None
            if request.media_type.value == "movie" and request.tmdb_id:
                media_details_task = asyncio.create_task(
                    overseerr_service.get_media_details("movie", request.tmdb_id)
                )
            elif request.media_type.value == "tv" and request.tmdb_id:
                media_details_task = asyncio.create_task(
                    overseerr_service.get_media_details("tv", request.tmdb_id)
                )

            ov = await ov_task
            media: dict[str, object] = {}
            request_status = "unknown"
            if ov:
                media = ov.get("media") or {}
                request_status = overseerr_service.normalize_media_status(media.get("status"))

            media_details = await media_details_task if media_details_task else None
            merged_media = {**media, **(media_details or {})}
            return DashboardOverseerrDetails(
                overview=merged_media.get("overview") or merged_media.get("summary") or "",
                poster=build_poster_url(merged_media.get("posterPath") or merged_media.get("poster")),
                status=request_status,
                url=build_overseerr_media_url(
                    self.settings.overseerr_url,
                    request.media_type.value,
                    request.tmdb_id,
                ),
            )
        finally:
            await overseerr_service.close()

    async def _load_serialized_stored_releases(
        self,
        request_id: int,
        *,
        media_type: MediaType,
    ) -> list[dict[str, object]]:
        from app.siftarr.models.release import Release

        release_result = await self.db.execute(
            select(Release)
            .where(Release.request_id == request_id)
            .order_by(
                Release.score.desc(),
                Release.size.asc(),
                Release.seeders.desc(),
                Release.created_at.desc(),
            )
        )
        releases = list(release_result.scalars().all())
        engine = await self._build_rule_engine(media_type=media_type.value)
        return finalize_releases(
            [
                serialize_stored_evaluated_release(
                    release,
                    engine.evaluate(build_prowlarr_release(release)),
                    media_type=media_type,
                )
                for release in releases
            ]
        )

    async def _load_active_staged_payloads(
        self,
        request_id: int,
        *,
        media_type: MediaType,
        request_status: RequestStatus,
    ) -> list[dict[str, object]]:
        if not is_active_staging_workflow_status(request_status):
            return []

        result = await self.db.execute(
            select(StagedTorrent)
            .where(
                StagedTorrent.request_id == request_id,
                StagedTorrent.status.in_(["staged", "approved"]),
            )
            .order_by(StagedTorrent.updated_at.desc(), StagedTorrent.created_at.desc())
        )
        return [
            serialize_active_staged_torrent(staged_torrent, media_type=media_type)
            for staged_torrent in result.scalars().all()
        ]

    async def _load_tv_info(
        self,
        *,
        request_id: int,
        background_tasks: BackgroundTasks,
        releases: list[dict[str, object]],
    ) -> DashboardTVDetails:
        seasons, episodes = await load_tv_seasons_with_episodes(self.db, request_id)

        episodes_by_season: dict[int, list[Any]] = {}
        for episode in episodes:
            episodes_by_season.setdefault(episode.season_id, []).append(episode)

        sync_state = compute_sync_metadata(seasons, episodes_by_season, request_id, background_tasks)
        seasons_data = []
        known_season_numbers: list[int] = []
        for season in seasons:
            known_season_numbers.append(season.season_number)
            season_episodes = episodes_by_season.get(season.id, [])
            available_count = sum(
                1 for ep in season_episodes if ep.status == RequestStatus.COMPLETED
            )
            state_counts = count_season_episode_states(season_episodes)
            seasons_data.append(
                {
                    "id": season.id,
                    "season_number": season.season_number,
                    "status": season.status.value,
                    "available_count": available_count,
                    "total_count": len(season_episodes),
                    "pending_count": state_counts["pending"],
                    "unreleased_count": state_counts["unreleased"],
                    "episodes": [
                        {
                            "id": ep.id,
                            "episode_number": ep.episode_number,
                            "title": ep.title,
                            "air_date": ep.air_date.isoformat() if ep.air_date else None,
                            "status": ep.status.value,
                        }
                        for ep in season_episodes
                    ],
                }
            )

        self._apply_known_tv_release_metadata(releases, known_season_numbers)
        releases_by_season, releases_by_episode = self._group_tv_releases(releases, known_season_numbers)
        return DashboardTVDetails(
            seasons=seasons_data,
            releases_by_season={str(k): v for k, v in releases_by_season.items()},
            releases_by_episode={f"{k[0]}-{k[1]}": v for k, v in releases_by_episode.items()},
            sync_state=sync_state,
            aggregate_counts=count_request_episode_states(seasons_data),
        )

    def _apply_known_tv_release_metadata(
        self,
        releases: list[dict[str, object]],
        known_season_numbers: list[int],
    ) -> None:
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

    async def _load_timeline(self, request_id: int) -> list[DashboardTimelineEntry]:
        activity_service = ActivityLogService(self.db)
        timeline_entries = await activity_service.get_timeline(request_id, limit=200)
        timeline_entries.reverse()
        return [
            DashboardTimelineEntry(
                id=entry.id,
                event_type=entry.event_type,
                details=json.loads(entry.details) if entry.details else None,
                created_at=entry.created_at.isoformat() if entry.created_at else None,
            )
            for entry in timeline_entries
        ]

    async def _known_total_seasons(self, request_id: int) -> int | None:
        from app.siftarr.models.season import Season

        seasons_result = await self.db.execute(
            select(Season).where(Season.request_id == request_id).order_by(Season.season_number)
        )
        seasons = list(seasons_result.scalars().all())
        return len(seasons) or None

    async def _build_rule_engine(self, *, media_type: str) -> RuleEngine:
        rules_result = await self.db.execute(select(Rule))
        rules = list(rules_result.scalars().all())
        return RuleEngine.from_db_rules(rules=rules, media_type=media_type)

    async def _search_tv(
        self,
        request: Any,
        *,
        season: int | None = None,
        episode: int | None = None,
    ) -> Any:
        tvdb_id = ensure_tvdb_id(request)
        prowlarr = ProwlarrService(settings=self.settings)
        try:
            return await prowlarr.search_by_tvdbid(
                tvdbid=tvdb_id,
                title=request.title,
                season=season,
                episode=episode,
                year=request.year,
            )
        finally:
            await prowlarr.close()


def serialize_request_details_response(data: RequestDetailsData) -> dict[str, object]:
    """Convert request-details service DTOs into JSON-ready payloads."""
    payload: dict[str, object] = {
        "request": {
            "id": data.request.id,
            "title": data.request.title,
            "status": data.request.status,
            "media_type": data.request.media_type,
        },
        "releases": data.releases,
        "active_staged_torrent": data.active_staged_torrent,
        "active_staged_torrents": data.active_staged_torrents,
        "timeline": [
            {
                "id": entry.id,
                "event_type": entry.event_type,
                "details": entry.details,
                "created_at": entry.created_at,
            }
            for entry in data.timeline
        ],
    }
    if data.overseerr is not None:
        payload["overseerr"] = {
            "overview": data.overseerr.overview,
            "poster": data.overseerr.poster,
            "status": data.overseerr.status,
            "url": data.overseerr.url,
        }
    if data.tv_info is not None:
        payload["tv_info"] = {
            "seasons": data.tv_info.seasons,
            "releases_by_season": data.tv_info.releases_by_season,
            "releases_by_episode": data.tv_info.releases_by_episode,
            "sync_state": data.tv_info.sync_state,
            "aggregate_counts": data.tv_info.aggregate_counts,
        }
    return payload


def serialize_request_search_response(data: RequestSearchData) -> dict[str, object]:
    """Convert movie-search service DTOs into JSON-ready payloads."""
    return {
        "releases": data.releases,
        "request": {
            "id": data.request.id,
            "title": data.request.title,
            "status": data.request.status,
            "media_type": data.request.media_type,
        },
    }


def serialize_tv_search_response(data: TVSearchData) -> dict[str, object]:
    """Convert TV-search service DTOs into JSON-ready payloads."""
    payload: dict[str, object] = {"releases": data.releases}
    if data.error is not None:
        payload["error"] = data.error
    if data.known_total_seasons is not None:
        payload["known_total_seasons"] = data.known_total_seasons
    return payload
