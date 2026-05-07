"""Search service for request processing and manual release selection."""

import logging
from copy import copy
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.siftarr.config import get_settings
from app.siftarr.models import EventType
from app.siftarr.models.release import Release
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
    cached_parse_release_coverage,
    is_exact_single_episode_release,
)
from app.siftarr.services.release_serializers import (
    apply_release_size_per_season_metadata,
    finalize_releases,
    season_pack_release_sort_key,
    serialize_evaluated_release,
    serialize_stored_evaluated_release,
)
from app.siftarr.services.release_storage import (
    get_release_persistence_key,
    persist_manual_release,
    store_search_results,
)
from app.siftarr.services.rule_engine import (
    ReleaseEvaluation,
    RuleEngine,
    get_cached_engine,
    set_cached_engine,
)
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
        engine = await self._build_rule_engine(media_type=request.media_type.value)
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
        evaluations = []
        coverages = []
        for release in result.releases:
            coverage = cached_parse_release_coverage(release.title)
            if coverage.episode_number is not None:
                continue
            if coverage.is_complete_series:
                continue
            if coverage.season_numbers != (season_number,):
                continue
            evaluations.append(self._evaluation_for_release(engine.evaluate(release), release))
            coverages.append(coverage)
        scope: dict[str, object] = {"type": "season_packs", "season_number": season_number}
        releases = await self._persist_and_serialize_tv_evaluations(
            request.id, evaluations, scope=scope, coverages=coverages
        )
        return TVSearchData(
            releases=finalize_releases(releases, sort_key=season_pack_release_sort_key),
            scope=scope,
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
        evaluations = []
        coverages = []
        for release in result.releases:
            coverage = cached_parse_release_coverage(release.title)
            if coverage.episode_number is not None:
                continue
            if not coverage.is_complete_series and len(coverage.season_numbers) <= 1:
                continue
            evaluations.append(self._evaluation_for_release(engine.evaluate(release), release))
            coverages.append(coverage)
        scope: dict[str, object] = {"type": "multi_season_packs"}
        releases = await self._persist_and_serialize_tv_evaluations(
            request.id,
            evaluations,
            scope=scope,
            coverages=coverages,
            known_total_seasons=known_total_seasons,
        )
        return TVSearchData(
            releases=finalize_releases(releases, sort_key=season_pack_release_sort_key),
            known_total_seasons=known_total_seasons,
            scope=scope,
        )

    async def search_episode(
        self,
        request: Any,
        *,
        season_number: int,
        episode_number: int,
    ) -> TVSearchData:
        """Search for a specific single-episode release.

        Passing releases are automatically staged via the rule engine
        selection (``selection_source="rule"``).
        """
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
        evaluations = []
        coverages = []
        for release in result.releases:
            coverage = cached_parse_release_coverage(release.title)
            if coverage.is_complete_series:
                continue
            if coverage.season_numbers != (season_number,):
                continue
            if coverage.episode_number != episode_number:
                continue
            if not is_exact_single_episode_release(release.title, season_number, episode_number):
                continue
            evaluations.append(self._evaluation_for_release(engine.evaluate(release), release))
            coverages.append(coverage)
        scope: dict[str, object] = {
            "type": "single_episode",
            "season_number": season_number,
            "episode_number": episode_number,
        }
        releases = await self._persist_and_serialize_tv_evaluations(
            request.id, evaluations, scope=scope, coverages=coverages
        )

        # ── Auto-stage the best passing release ────────────────────────
        passing_evals = [e for e in evaluations if e.passed]
        if passing_evals:
            best_eval = max(passing_evals, key=lambda e: e.total_score)
            stored_release = await self._find_stored_release(request.id, best_eval)
            if stored_release is not None:
                try:
                    await StagingService(self.db).use_releases(
                        request,
                        [stored_release],
                        selection_source="rule",
                    )
                    logger.info(
                        "Auto-staged episode release: request_id=%s season=%s episode=%s title=%s",
                        request.id,
                        season_number,
                        episode_number,
                        best_eval.release.title,
                    )
                except Exception:
                    logger.exception(
                        "Failed to auto-stage episode release: request_id=%s season=%s "
                        "episode=%s title=%s",
                        request.id,
                        season_number,
                        episode_number,
                        best_eval.release.title,
                    )

        return TVSearchData(
            releases=finalize_releases(releases),
            scope=scope,
        )

    async def _find_stored_release(
        self,
        request_id: int,
        evaluation: ReleaseEvaluation,
    ) -> Release | None:
        """Look up the stored :class:`Release` record for an evaluation.

        Returns *None* when the record is not found or the query fails
        (caller degrades gracefully).
        """
        try:
            result = await self.db.execute(
                select(Release).where(
                    Release.request_id == request_id,
                    Release.title == evaluation.release.title,
                )
            )
            return result.scalar_one_or_none()
        except Exception:
            logger.warning(
                "Could not find stored release for auto-stage: request_id=%s title=%s",
                request_id,
                evaluation.release.title,
                exc_info=True,
            )
            return None

    async def _persist_and_serialize_tv_evaluations(
        self,
        request_id: int,
        evaluations: list[ReleaseEvaluation],
        *,
        scope: dict[str, object],
        coverages: list[Any],
        known_total_seasons: int | None = None,
    ) -> list[dict[str, object]]:
        """Persist scoped TV evaluations and serialize with IDs; tolerate pure mocks."""
        try:
            stored_by_key = await store_search_results(
                self.db, request_id, evaluations, scope=scope
            )
        except (StopAsyncIteration, StopIteration):
            releases = [
                serialize_evaluated_release(
                    evaluation.release,
                    evaluation,
                    coverage=coverages[index] if index < len(coverages) else None,
                    known_total_seasons=known_total_seasons,
                )
                for index, evaluation in enumerate(evaluations)
            ]
        else:
            releases = [
                serialize_stored_evaluated_release(stored, evaluation, media_type=MediaType.TV)
                for evaluation in evaluations
                if (
                    stored := stored_by_key.get(
                        get_release_persistence_key(
                            title=evaluation.release.title, info_hash=evaluation.release.info_hash
                        )
                    )
                )
                is not None
            ]

        if known_total_seasons is not None:
            for release in releases:
                release["known_total_seasons"] = known_total_seasons
                apply_release_size_per_season_metadata(release)
        return releases

    def _evaluation_for_release(
        self, evaluation: ReleaseEvaluation, release: ProwlarrRelease
    ) -> ReleaseEvaluation:
        """Return an evaluation object bound to the release, isolating shared mocks."""
        bound = copy(evaluation)
        bound.release = release
        return bound

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
        """Load rules from DB and build a RuleEngine (cached)."""
        cached = get_cached_engine(media_type)
        if cached is not None:
            return cached

        rules_result = await self.db.execute(select(Rule))
        rules = list(rules_result.scalars().all())
        engine = RuleEngine.from_db_rules(rules=rules, media_type=media_type)
        set_cached_engine(media_type, engine)
        return engine
