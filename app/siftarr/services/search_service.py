"""Search service for request processing and manual release selection."""

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.siftarr.config import get_settings
from app.siftarr.models import EventType
from app.siftarr.models.request import MediaType
from app.siftarr.models.request import Request as RequestModel
from app.siftarr.models.rule import Rule
from app.siftarr.services.activity_log_service import ActivityLogService
from app.siftarr.services.dashboard_service import TVSearchData
from app.siftarr.services.lifecycle_service import LifecycleService
from app.siftarr.services.media_helpers import extract_media_title_and_year
from app.siftarr.services.movie_decision_service import MovieDecisionService
from app.siftarr.services.overseerr_service import OverseerrService
from app.siftarr.services.pending_queue_service import PendingQueueService
from app.siftarr.services.prowlarr_service import ProwlarrRelease, ProwlarrService
from app.siftarr.services.qbittorrent_service import QbittorrentService
from app.siftarr.services.release_parser import (
    is_exact_single_episode_release,
    parse_release_coverage,
)
from app.siftarr.services.release_serializers import (
    finalize_releases,
    season_pack_release_sort_key,
    serialize_evaluated_release,
)
from app.siftarr.services.release_storage import persist_manual_release
from app.siftarr.services.rule_engine import ReleaseEvaluation, RuleEngine
from app.siftarr.services.staging_service import StagingService
from app.siftarr.services.tv_decision_service import TVDecisionService
from app.siftarr.services.tv_enrichment_service import TVEnrichmentService

logger = logging.getLogger(__name__)


class SearchService:
    """Service for running torrent searches and processing manual release selections."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def evaluate_manual_release(
        self,
        request: RequestModel,
        release: ProwlarrRelease,
    ) -> ReleaseEvaluation:
        """Evaluate an ad hoc release using the request media type rules."""
        rules_result = await self.db.execute(select(Rule))
        rules = list(rules_result.scalars().all())
        engine = RuleEngine.from_db_rules(rules=rules, media_type=request.media_type.value)
        return engine.evaluate(release)

    async def select_manual_release(
        self,
        request: RequestModel,
        release: ProwlarrRelease,
    ) -> dict[str, object]:
        """Persist and use a manual-search release through the normal selection path."""
        evaluation = await self.evaluate_manual_release(request, release)
        stored_release = await persist_manual_release(self.db, request, release, evaluation)
        return await StagingService(self.db).use_releases(
            request, [stored_release], selection_source="manual"
        )

    async def process_request_search(
        self,
        request: RequestModel,
    ) -> dict:
        """Run torrent search for a request and clean up queue state on success."""
        activity_log = ActivityLogService(self.db)
        await activity_log.log(
            EventType.SEARCH_STARTED,
            request_id=request.id,
            details={"title": request.title, "media_type": request.media_type.value},
        )

        runtime_settings = get_settings()

        # Backfill year if missing (e.g. Overseerr was unreachable at creation time)
        if request.year is None and (request.tmdb_id or request.tvdb_id):
            overseerr = OverseerrService(settings=runtime_settings)
            try:
                media_type_for_api = "movie" if request.media_type == MediaType.MOVIE else "tv"
                media_id = request.tmdb_id or request.tvdb_id
                if media_id is None:
                    return {}
                _, year = await extract_media_title_and_year(
                    overseerr, media_type_for_api, media_id
                )
                if year is not None:
                    lifecycle = LifecycleService(self.db)
                    await lifecycle.update_request_metadata(request.id, year=year)
                    await self.db.refresh(request)
            except Exception:
                pass

        prowlarr_service = ProwlarrService(settings=runtime_settings)
        qbittorrent_service = QbittorrentService(settings=runtime_settings)
        queue_service = PendingQueueService(self.db)

        if request.media_type.value == "movie":
            decision_service = MovieDecisionService(self.db, prowlarr_service, qbittorrent_service)
            result = await decision_service.process_request(request.id)
        else:
            decision_service = TVDecisionService(self.db, prowlarr_service, qbittorrent_service)
            # Dashboard-triggered searches for TV shows should only search for
            # season packs and multi-season packs, not individual episodes.
            # Individual episode searching is done from the details modal.
            result = await decision_service.process_request(request.id, search_episodes=False)

        activity_log = ActivityLogService(self.db)
        await activity_log.log(
            EventType.SEARCH_COMPLETED,
            request_id=request.id,
            details={
                "status": result.get("status"),
                "message": result.get("message"),
            },
        )

        if result.get("status") == "completed":
            await queue_service.remove_from_queue(request.id)

        return result

    async def search_season_packs(self, request: Any, *, season_number: int) -> TVSearchData:
        """Search for season packs covering exactly one season."""
        result = await self._search_tv(request, season=season_number)
        if result.error:
            return TVSearchData(
                releases=[],
                scope={"type": "season_packs", "season_number": season_number},
                error=result.error,
            )

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
            releases=finalize_releases(releases, sort_key=season_pack_release_sort_key),
            scope={"type": "season_packs", "season_number": season_number},
        )

    async def search_multi_season_packs(self, request: Any, *, request_id: int) -> TVSearchData:
        """Search for multi-season packs covering 2+ seasons or complete series."""
        tv_enrichment = TVEnrichmentService(self.db)
        known_total_seasons = await tv_enrichment.known_total_seasons(request_id)
        result = await self._search_tv(request)
        if result.error:
            return TVSearchData(
                releases=[],
                known_total_seasons=known_total_seasons,
                scope={"type": "multi_season_packs"},
                error=result.error,
            )

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
            scope={"type": "multi_season_packs"},
        )

    async def search_episode(
        self,
        request: Any,
        *,
        season_number: int,
        episode_number: int,
    ) -> TVSearchData:
        """Search for a specific single-episode release."""
        result = await self._search_tv(request, season=season_number, episode=episode_number)
        if result.error:
            return TVSearchData(
                releases=[],
                scope={
                    "type": "single_episode",
                    "season_number": season_number,
                    "episode_number": episode_number,
                },
                error=result.error,
            )

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
        return TVSearchData(
            releases=finalize_releases(releases),
            scope={
                "type": "single_episode",
                "season_number": season_number,
                "episode_number": episode_number,
            },
        )

    async def _search_tv(
        self,
        request: Any,
        *,
        season: int | None = None,
        episode: int | None = None,
        cacheable: bool = False,
    ) -> Any:
        """Execute a Prowlarr TV search by TVDB ID.

        Args:
            request: The request model.
            season: Optional season number.
            episode: Optional episode number.
            cacheable: Whether the result may be cached.  Dashboard-triggered
                searches (the common case) pass ``False`` so the user always
                sees fresh results.
        """
        from app.siftarr.services.request_service import ensure_tvdb_id

        tvdb_id = ensure_tvdb_id(request)
        runtime_settings = get_settings()
        prowlarr = ProwlarrService(settings=runtime_settings)
        return await prowlarr.search_by_tvdbid(
            tvdbid=tvdb_id,
            title=request.title,
            season=season,
            episode=episode,
            year=request.year,
            cacheable=cacheable,
        )

    async def _build_rule_engine(self, *, media_type: str) -> RuleEngine:
        """Load rules from DB and build a RuleEngine."""
        rules_result = await self.db.execute(select(Rule))
        rules = list(rules_result.scalars().all())
        return RuleEngine.from_db_rules(rules=rules, media_type=media_type)
