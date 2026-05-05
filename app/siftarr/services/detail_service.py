"""Request detail loading for dashboard API responses."""

from __future__ import annotations

import json
from typing import Any

from fastapi import BackgroundTasks
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.siftarr.config import Settings, get_settings
from app.siftarr.models.request import MediaType, RequestStatus, is_active_staging_workflow_status
from app.siftarr.models.rule import Rule
from app.siftarr.models.staged_torrent import StagedTorrent
from app.siftarr.services.activity_log_service import ActivityLogService
from app.siftarr.services.dashboard_service import (
    DashboardRequestSummary,
    DashboardTimelineEntry,
    RequestDetailsData,
    RequestSearchData,
)
from app.siftarr.services.metadata_service import MetadataService
from app.siftarr.services.release_serializers import (
    apply_active_selection_metadata,
    finalize_releases,
    serialize_active_staged_torrent,
    serialize_stored_evaluated_release,
)
from app.siftarr.services.release_storage import build_prowlarr_release
from app.siftarr.services.rule_engine import RuleEngine, get_cached_engine, set_cached_engine
from app.siftarr.services.tv_enrichment_service import TVEnrichmentService


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
    ) -> RequestDetailsData:
        """Load full request detail payload including releases, TV info, and timeline."""
        releases, total_releases = await self._load_serialized_stored_releases(
            request_id, media_type=request.media_type, offset=offset, limit=limit
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
            tv_enrichment = TVEnrichmentService(self.db)
            tv_info = await tv_enrichment.load_tv_info(
                request_id=request_id,
                background_tasks=background_tasks,
                releases=releases,
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
        releases, _total = await self._load_serialized_stored_releases(
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
    ) -> tuple[list[dict[str, object]], int]:
        """Load and serialize persisted releases with rule evaluation.

        Returns (serialized_releases, total_count) for pagination UI.
        """
        from app.siftarr.models.release import Release

        # Get total count for pagination
        count_result = await self.db.execute(
            select(func.count()).select_from(Release).where(Release.request_id == request_id)
        )
        total_count = count_result.scalar() or 0

        release_result = await self.db.execute(
            select(Release)
            .where(Release.request_id == request_id)
            .order_by(
                Release.score.desc(),
                Release.size.asc(),
                Release.seeders.desc(),
                Release.created_at.desc(),
            )
            .offset(offset)
            .limit(limit)
        )
        releases = list(release_result.scalars().all())
        engine = await self._build_rule_engine(media_type=media_type.value)
        return (
            finalize_releases(
                [
                    serialize_stored_evaluated_release(
                        release,
                        engine.evaluate(build_prowlarr_release(release)),
                        media_type=media_type,
                    )
                    for release in releases
                ]
            ),
            total_count,
        )

    async def _load_active_staged_payloads(
        self,
        request_id: int,
        *,
        media_type: MediaType,
        request_status: RequestStatus,
    ) -> list[dict[str, object]]:
        """Load active staged torrent payloads for a request."""
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
