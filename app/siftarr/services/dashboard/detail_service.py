"""Request detail loading for dashboard API responses."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from fastapi import BackgroundTasks
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.siftarr.config import Settings, get_settings
from app.siftarr.models.request import MediaType, RequestStatus, is_active_staging_workflow_status
from app.siftarr.models.rule import Rule
from app.siftarr.models.staged_torrent import StagedTorrent
from app.siftarr.services.dashboard.dashboard_service import (
    DashboardRequestSummary,
    DashboardTimelineEntry,
    RequestDetailsData,
    RequestSearchData,
)
from app.siftarr.services.dashboard.tv_enrichment_service import TVEnrichmentService
from app.siftarr.services.decisions.rule_engine import (
    RuleEngine,
    get_cached_engine,
    set_cached_engine,
)
from app.siftarr.services.lifecycle.activity_log_service import ActivityLogService
from app.siftarr.services.metadata_service import MetadataService
from app.siftarr.services.releases.release_serializers import (
    apply_active_selection_metadata,
    finalize_releases,
    serialize_active_staged_torrent,
    serialize_stored_evaluated_release,
)
from app.siftarr.services.releases.release_storage import build_prowlarr_release

logger = logging.getLogger(__name__)

DETAIL_SORT_KEYS = {"score", "size", "seeders", "published", "title", "indexer"}
DETAIL_SORT_DIRECTIONS = {"asc", "desc"}
RESOLUTION_ALIASES = {
    "4k": "2160p",
    "uhd": "2160p",
    "2160": "2160p",
    "2160p": "2160p",
    "1080": "1080p",
    "1080p": "1080p",
    "fullhd": "1080p",
    "720": "720p",
    "720p": "720p",
    "480": "480p",
    "480p": "480p",
}


@dataclass(frozen=True, slots=True)
class DetailReleaseControls:
    title: str = ""
    resolution: str = "all"
    sort: str = "score"
    direction: str = "desc"

    @classmethod
    def normalize(
        cls,
        *,
        title: str | None = None,
        resolution: str | None = None,
        sort: str | None = None,
        direction: str | None = None,
    ) -> DetailReleaseControls:
        normalized_title = (title or "").strip()[:200]
        normalized_resolution = RESOLUTION_ALIASES.get(
            (resolution or "all").strip().casefold(), "all"
        )
        normalized_sort = (sort or "score").strip().casefold()
        if normalized_sort not in DETAIL_SORT_KEYS:
            normalized_sort = "score"
        normalized_direction = (direction or "desc").strip().casefold()
        if normalized_direction not in DETAIL_SORT_DIRECTIONS:
            normalized_direction = "desc"
        return cls(
            title=normalized_title,
            resolution=normalized_resolution,
            sort=normalized_sort,
            direction=normalized_direction,
        )

    def as_payload(self, *, offset: int, limit: int) -> dict[str, object]:
        return {
            "title": self.title,
            "resolution": self.resolution,
            "sort": self.sort,
            "direction": self.direction,
            "offset": offset,
            "limit": limit,
        }


class _ReverseSort:
    """Small wrapper for descending sort inside a tuple key."""

    def __init__(self, value: Any) -> None:
        self.value = value

    def __lt__(self, other: object) -> bool:
        other_value: Any = other.value if isinstance(other, _ReverseSort) else other
        return bool(other_value < self.value)


class DetailService:
    """Load dashboard request-detail data while keeping routers thin."""

    def __init__(self, db: AsyncSession, *, settings: Settings | None = None) -> None:
        self.db = db
        self.settings = settings or get_settings()

    async def load_request_details(
        self,
        request: Any,
        *,
        request_id: int,
        background_tasks: BackgroundTasks,
        offset: int = 0,
        limit: int = 100,
        title: str | None = None,
        resolution: str | None = None,
        sort: str | None = None,
        direction: str | None = None,
    ) -> RequestDetailsData:
        """Load full request detail payload including releases, TV info, and timeline."""
        controls = DetailReleaseControls.normalize(
            title=title, resolution=resolution, sort=sort, direction=direction
        )
        (
            releases,
            total_releases,
            filtered_total_releases,
        ) = await self._load_serialized_stored_releases(
            request_id,
            media_type=request.media_type,
            offset=offset,
            limit=limit,
            controls=controls,
        )
        active_staged_torrents = await self._load_active_staged_payloads(
            request_id,
            media_type=request.media_type,
            request_status=request.status,
        )
        apply_active_selection_metadata(
            releases, active_staged_torrents, media_type=request.media_type
        )

        tv_info = None
        if request.media_type == MediaType.TV:
            tv_releases = releases
            if filtered_total_releases > len(releases):
                tv_releases, _, _ = await self._load_serialized_stored_releases(
                    request_id,
                    media_type=request.media_type,
                    offset=0,
                    limit=filtered_total_releases,
                    controls=controls,
                )
                apply_active_selection_metadata(
                    tv_releases, active_staged_torrents, media_type=request.media_type
                )
            tv_enrichment = TVEnrichmentService(self.db)
            tv_info = await tv_enrichment.load_tv_info(
                request_id=request_id,
                background_tasks=background_tasks,
                releases=tv_releases,
                active_staged_torrents=active_staged_torrents,
            )

        metadata_service = MetadataService(settings=self.settings)
        overseerr_details = await metadata_service.load_overseerr_details(request)

        return RequestDetailsData(
            request=DashboardRequestSummary(
                id=request.id,
                title=request.title,
                status=request.status.value,
                media_type=request.media_type.value,
            ),
            releases=releases,
            total_releases=total_releases,
            filtered_total_releases=filtered_total_releases,
            release_controls=controls.as_payload(offset=offset, limit=limit),
            active_staged_torrent=active_staged_torrents[0] if active_staged_torrents else None,
            active_staged_torrents=active_staged_torrents,
            overseerr=overseerr_details,
            tv_info=tv_info,
            timeline=await self._load_timeline(request_id),
        )

    async def load_movie_search_results(
        self, request: Any, *, request_id: int
    ) -> RequestSearchData:
        """Load stored releases for a movie request after a search."""
        releases, _total, _filtered_total = await self._load_serialized_stored_releases(
            request_id, media_type=request.media_type
        )
        return RequestSearchData(
            request=DashboardRequestSummary(
                id=request.id,
                title=request.title,
                status=request.status.value,
                media_type=request.media_type.value,
            ),
            releases=releases,
        )

    async def _load_serialized_stored_releases(
        self,
        request_id: int,
        *,
        media_type: MediaType,
        offset: int = 0,
        limit: int = 100,
        controls: DetailReleaseControls | None = None,
    ) -> tuple[list[dict[str, object]], int, int]:
        """Load and serialize persisted releases with rule evaluation.

        Returns (serialized_releases, total_count) for pagination UI.
        """
        from app.siftarr.models.release import Release

        controls = controls or DetailReleaseControls.normalize()

        # Get total count for pagination
        count_result = await self.db.execute(
            select(func.count()).select_from(Release).where(Release.request_id == request_id)
        )
        total_count = count_result.scalar() or 0

        filters = [Release.request_id == request_id]
        if controls.title:
            filters.append(Release.title.ilike(f"%{controls.title}%"))
        if controls.resolution != "all":
            filters.append(func.lower(Release.resolution) == controls.resolution.casefold())

        filtered_total = total_count
        if len(filters) > 1:
            filtered_count_result = await self.db.execute(
                select(func.count()).select_from(Release).where(*filters)
            )
            filtered_total = filtered_count_result.scalar() or 0

        sort_columns = {
            "score": Release.score,
            "size": Release.size,
            "seeders": Release.seeders,
            "published": Release.publish_date,
            "title": Release.title,
            "indexer": Release.indexer,
        }
        primary = sort_columns[controls.sort]
        primary_order = primary.asc() if controls.direction == "asc" else primary.desc()

        release_result = await self.db.execute(
            select(Release)
            .where(*filters)
            .order_by(
                primary_order,
                Release.score.desc(),
                Release.size.asc(),
                Release.seeders.desc(),
                Release.publish_date.desc(),
                Release.title.asc(),
            )
            .offset(offset)
            .limit(limit)
        )
        releases = list(release_result.scalars().all())
        logger.info(
            "Stored releases loaded from DB: request_id=%s media_type=%s offset=%s limit=%s count=%s total=%s filtered_total=%s source=db",
            request_id,
            media_type.value,
            offset,
            limit,
            len(releases),
            total_count,
            filtered_total,
        )
        engine = await self._build_rule_engine(media_type=media_type.value)
        serialized = [
            serialize_stored_evaluated_release(
                release,
                engine.evaluate(build_prowlarr_release(release)),
                media_type=media_type,
            )
            for release in releases
        ]
        return (
            finalize_releases(
                serialized,
                sort_key=self._serialized_sort_key(controls),
            ),
            total_count,
            filtered_total,
        )

    def _serialized_sort_key(self, controls: DetailReleaseControls):
        if controls.sort == "score" and controls.direction == "desc":
            return None

        def sort_value(release: dict[str, object]) -> object:
            if controls.sort == "published":
                return release.get("publish_date") or ""
            if controls.sort == "size":
                return release.get("_size_bytes") or 0
            if controls.sort == "seeders":
                return release.get("seeders") or 0
            if controls.sort == "title":
                return str(release.get("title") or "").casefold()
            if controls.sort == "indexer":
                return str(release.get("indexer") or "").casefold()
            return release.get("score") or 0

        reverse = controls.direction == "desc"
        return lambda release: (
            sort_value(release) if not reverse else _ReverseSort(sort_value(release)),
            str(release.get("title") or "").casefold(),
        )

    async def _load_active_staged_payloads(
        self,
        request_id: int,
        *,
        media_type: MediaType,
        request_status: RequestStatus,
    ) -> list[dict[str, object]]:
        """Load active staged torrent payloads for a request."""
        if media_type != MediaType.TV and not is_active_staging_workflow_status(request_status):
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

    async def _load_timeline(self, request_id: int) -> list[DashboardTimelineEntry]:
        """Load and format activity timeline for a request."""
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

    async def _build_rule_engine(self, *, media_type: str) -> RuleEngine:
        """Load rules from DB and build a RuleEngine for evaluation (cached)."""
        cached = get_cached_engine(media_type)
        if cached is not None:
            return cached

        rules_result = await self.db.execute(select(Rule))
        rules = list(rules_result.scalars().all())
        engine = RuleEngine.from_db_rules(rules=rules, media_type=media_type)
        set_cached_engine(media_type, engine)
        return engine
