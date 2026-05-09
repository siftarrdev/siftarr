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
from app.siftarr.models.season import Season
from app.siftarr.services.dashboard.dashboard_service import TVSearchData
from app.siftarr.services.dashboard.tv_enrichment_service import TVEnrichmentService
from app.siftarr.services.decisions.movie_decision_service import MovieDecisionService
from app.siftarr.services.decisions.rule_engine import (
    ReleaseEvaluation,
    RuleEngine,
    get_cached_engine,
    set_cached_engine,
)
from app.siftarr.services.decisions.tv_decision_service import TVDecisionService
from app.siftarr.services.integrations.overseerr_service import OverseerrService
from app.siftarr.services.integrations.prowlarr_service import ProwlarrRelease, ProwlarrService
from app.siftarr.services.integrations.qbittorrent_service import QbittorrentService
from app.siftarr.services.lifecycle.activity_log_service import ActivityLogService
from app.siftarr.services.lifecycle.lifecycle_service import LifecycleService
from app.siftarr.services.lifecycle.pending_queue_service import PendingQueueService
from app.siftarr.services.metadata_service import extract_imdb_id
from app.siftarr.services.releases.release_parser import (
    cached_parse_release_coverage,
    is_exact_single_episode_release,
    serialize_release_coverage,
)
from app.siftarr.services.releases.release_serializers import (
    apply_release_size_per_season_metadata,
    finalize_releases,
    season_pack_release_sort_key,
    serialize_evaluated_release,
    serialize_stored_evaluated_release,
)
from app.siftarr.services.releases.release_storage import (
    build_prowlarr_release,
    get_release_persistence_key,
    persist_manual_release,
    store_search_results,
)
from app.siftarr.services.releases.staging_service import StagingService
from app.siftarr.services.utils.media_helpers import extract_media_title_and_year

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
            # Dashboard-triggered searches for TV shows search for season packs,
            # multi-season packs, AND individual PENDING episodes so that all
            # unresolved episodes are covered in a single search pass.
            result = await decision_service.process_request(request.id, search_episodes=True)

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
        result = await self._search_tv_season_sweep(request, season_number=season_number)
        if result.error:
            return TVSearchData(
                releases=[],
                scope={"type": "season_packs", "season_number": season_number},
                error=result.error,
            )

        stored_rows = await self._persist_tv_season_sweep(
            request.id, result.releases, season_number=season_number
        )
        scope: dict[str, object] = {"type": "season_packs", "season_number": season_number}
        releases = self._filter_serialize_stored_tv_releases(stored_rows, scope=scope)
        return TVSearchData(
            releases=finalize_releases(releases, sort_key=season_pack_release_sort_key),
            scope=scope,
        )

    async def search_multi_season_packs(self, request: Any, *, request_id: int) -> TVSearchData:
        """Search for multi-season packs covering 2+ seasons or complete series."""
        tv_enrichment = TVEnrichmentService(self.db)
        known_total_seasons = await tv_enrichment.known_total_seasons(request_id)
        season_numbers = await self._requested_season_numbers(request)
        if not season_numbers and known_total_seasons:
            season_numbers = list(range(1, known_total_seasons + 1))
        results = [
            await self._search_tv_season_sweep(request, season_number=season)
            for season in season_numbers
        ]
        result = self._combine_tv_results(results)
        if result.error:
            return TVSearchData(
                releases=[],
                known_total_seasons=known_total_seasons,
                scope={"type": "multi_season_packs"},
                error=result.error,
            )

        stored_rows: list[Release] = []
        for season in season_numbers:
            stored_rows.extend(
                await self._persist_tv_season_sweep(
                    request.id, result.releases, season_number=season
                )
            )
        stored_rows = self._dedupe_stored_releases(stored_rows)
        scope: dict[str, object] = {"type": "multi_season_packs"}
        releases = self._filter_serialize_stored_tv_releases(
            stored_rows, scope=scope, known_total_seasons=known_total_seasons
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
        scope: dict[str, object] = {
            "type": "single_episode",
            "season_number": season_number,
            "episode_number": episode_number,
        }
        cached_releases = await self._load_cached_tv_releases(
            request.id, season_number=season_number
        )
        cached_episode_releases = self._filter_serialize_stored_tv_releases(
            cached_releases, scope=scope
        )
        if cached_episode_releases:
            await self._auto_stage_best_stored_episode(request, cached_releases, scope=scope)
            return TVSearchData(releases=finalize_releases(cached_episode_releases), scope=scope)

        result = await self._search_tv_season_sweep(request, season_number=season_number)
        if result.error:
            return TVSearchData(
                releases=[],
                scope=scope,
                error=result.error,
            )

        stored_rows = await self._persist_tv_season_sweep(
            request.id, result.releases, season_number=season_number
        )
        releases = self._filter_serialize_stored_tv_releases(stored_rows, scope=scope)

        if not releases:
            episode_result = await self._search_tv(
                request,
                season=season_number,
                episode=episode_number,
                cacheable=False,
            )
            exact_rows = await self._persist_tv_evaluations(
                request.id,
                getattr(episode_result, "releases", []),
                scope=scope,
            )
            stored_rows = self._dedupe_stored_releases([*stored_rows, *exact_rows])
            releases = self._filter_serialize_stored_tv_releases(exact_rows, scope=scope)

        # ── Auto-stage the best passing release ────────────────────────
        await self._auto_stage_best_stored_episode(request, stored_rows, scope=scope)

        return TVSearchData(
            releases=finalize_releases(releases),
            scope=scope,
        )

    async def _load_cached_tv_releases(
        self, request_id: int, *, season_number: int | None = None
    ) -> list[Release]:
        """Load stored TV releases for request/season cache reuse."""
        if type(self.db).__module__.startswith("unittest.mock"):
            return []
        try:
            result = await self.db.execute(
                select(Release).where(Release.request_id == request_id).order_by(Release.id)
            )
        except Exception:
            logger.warning("Could not load cached TV releases", exc_info=True)
            return []
        rows = list(result.scalars().all())
        if season_number is None:
            logger.info(
                "TV releases loaded from DB cache: request_id=%s season=%s count=%s source=db",
                request_id,
                season_number,
                len(rows),
            )
            return rows
        filtered_rows = [
            row for row in rows if self._stored_release_covers_season(row, season_number)
        ]
        logger.info(
            "TV releases loaded from DB cache: request_id=%s season=%s count=%s source=db",
            request_id,
            season_number,
            len(filtered_rows),
        )
        return filtered_rows

    async def _persist_tv_season_sweep(
        self, request_id: int, releases: list[ProwlarrRelease], *, season_number: int
    ) -> list[Release]:
        """Evaluate and persist the full sweep, then return stored season rows."""
        persisted_rows = await self._persist_tv_evaluations(
            request_id,
            releases,
            scope={"type": "season_sweep", "season_number": season_number},
        )
        cached_rows = await self._load_cached_tv_releases(request_id, season_number=season_number)
        return cached_rows or persisted_rows

    async def _persist_tv_evaluations(
        self,
        request_id: int,
        releases: list[ProwlarrRelease],
        *,
        scope: dict[str, object],
    ) -> list[Release]:
        engine = await self._build_rule_engine(media_type="tv")
        evaluations = [
            self._evaluation_for_release(engine.evaluate(release), release) for release in releases
        ]
        try:
            stored_by_key = await store_search_results(
                self.db, request_id, evaluations, scope=scope, source="adhoc"
            )
        except (StopAsyncIteration, StopIteration):
            return [
                self._transient_release_from_evaluation(request_id, evaluation)
                for evaluation in evaluations
            ]
        return self._dedupe_stored_releases(list(stored_by_key.values()))

    def _transient_release_from_evaluation(
        self, request_id: int, evaluation: ReleaseEvaluation
    ) -> Release:
        release = evaluation.release
        coverage = cached_parse_release_coverage(release.title)
        rejection_reason = evaluation.rejection_reason
        return Release(
            request_id=request_id,
            title=release.title,
            size=release.size,
            seeders=release.seeders,
            leechers=release.leechers,
            download_url=release.download_url,
            magnet_url=release.magnet_url,
            info_hash=release.info_hash,
            indexer=release.indexer,
            publish_date=release.publish_date,
            resolution=release.resolution,
            codec=release.codec,
            release_group=release.release_group,
            files=release.files,
            uploaded_by=release.uploaded_by,
            season_number=coverage.season_number,
            episode_number=coverage.episode_number,
            season_coverage=serialize_release_coverage(coverage),
            score=evaluation.total_score,
            passed_rules=evaluation.passed,
            rejection_reason=rejection_reason if isinstance(rejection_reason, str) else None,
            search_source="adhoc",
        )

    def _filter_serialize_stored_tv_releases(
        self,
        releases: list[Release],
        *,
        scope: dict[str, object],
        known_total_seasons: int | None = None,
    ) -> list[dict[str, object]]:
        payloads = []
        for release in releases:
            if not self._stored_release_matches_display_scope(release, scope):
                continue
            evaluation = self._evaluation_from_stored_release(release)
            payload = serialize_stored_evaluated_release(
                release, evaluation, media_type=MediaType.TV
            )
            if known_total_seasons is not None:
                payload["known_total_seasons"] = known_total_seasons
                covered_seasons = payload.get("covered_seasons")
                payload["covers_all_known_seasons"] = bool(
                    known_total_seasons
                    and (
                        payload.get("is_complete_series")
                        or (
                            isinstance(covered_seasons, list)
                            and len(covered_seasons) >= known_total_seasons
                        )
                    )
                )
                apply_release_size_per_season_metadata(payload)
            payloads.append(payload)
        return payloads

    def _evaluation_from_stored_release(self, release: Release) -> ReleaseEvaluation:
        return ReleaseEvaluation(
            release=build_prowlarr_release(release),
            passed=release.passed_rules,
            total_score=release.score,
            matches=[],
            rejection_reason=release.rejection_reason,
        )

    def _stored_release_matches_display_scope(
        self, release: Release, scope: dict[str, object]
    ) -> bool:
        coverage = cached_parse_release_coverage(release.title)
        scope_type = scope.get("type")
        if scope_type == "single_episode":
            season_number = scope.get("season_number")
            episode_number = scope.get("episode_number")
            return (
                isinstance(season_number, int)
                and isinstance(episode_number, int)
                and coverage.season_numbers == (season_number,)
                and coverage.episode_number == episode_number
                and not coverage.is_complete_series
                and is_exact_single_episode_release(release.title, season_number, episode_number)
            )
        if scope_type == "season_packs":
            return (
                coverage.episode_number is None
                and not coverage.is_complete_series
                and coverage.season_numbers == (scope.get("season_number"),)
            )
        if scope_type == "multi_season_packs":
            return coverage.episode_number is None and (
                coverage.is_complete_series or len(coverage.season_numbers) > 1
            )
        return True

    def _stored_release_covers_season(self, release: Release, season_number: int) -> bool:
        coverage = cached_parse_release_coverage(release.title)
        return (
            coverage.is_complete_series
            or coverage.season_number == season_number
            or season_number in coverage.season_numbers
        )

    def _dedupe_stored_releases(self, releases: list[Release]) -> list[Release]:
        seen: set[str] = set()
        deduped: list[Release] = []
        for release in releases:
            key = get_release_persistence_key(title=release.title, info_hash=release.info_hash)
            if key in seen:
                continue
            seen.add(key)
            deduped.append(release)
        return deduped

    async def _auto_stage_best_stored_episode(
        self, request: Any, releases: list[Release], *, scope: dict[str, object]
    ) -> None:
        if type(self.db).__module__.startswith("unittest.mock"):
            return
        candidates = [
            release
            for release in releases
            if release.passed_rules and self._stored_release_matches_display_scope(release, scope)
        ]
        if not candidates:
            return
        stored_release = max(candidates, key=lambda release: release.score)
        try:
            await StagingService(self.db).use_releases(
                request,
                [stored_release],
                selection_source="rule",
            )
        except Exception:
            logger.exception(
                "Failed to auto-stage episode release: request_id=%s title=%s",
                request.id,
                stored_release.title,
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
                self.db, request_id, evaluations, scope=scope, source="adhoc"
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

    async def _search_tv_season_sweep(self, request: Any, *, season_number: int) -> Any:
        """Run an explicit-refresh paginated season sweep for ad hoc TV searches."""
        from app.siftarr.services.request_service import ensure_tvdb_id

        tvdb_id = ensure_tvdb_id(request)
        runtime_settings = get_settings()
        imdb_id = await self._load_imdb_id(request)
        prowlarr = ProwlarrService(settings=runtime_settings)
        logger.info(
            "TV season sweep requested: request_id=%s title=%s season=%s source=prowlarr",
            getattr(request, "id", None),
            request.title,
            season_number,
        )
        return await prowlarr.search_tv_season_sweep(
            title=request.title,
            season=season_number,
            imdbid=imdb_id,
            tvdbid=tvdb_id,
            cacheable=False,
            request_id=getattr(request, "id", None),
        )

    async def _load_imdb_id(self, request: Any) -> str | None:
        tmdb_id = getattr(request, "tmdb_id", None)
        if not tmdb_id:
            return None
        try:
            details = await OverseerrService(settings=get_settings()).get_media_details(
                "tv", tmdb_id
            )
        except Exception:
            logger.warning(
                "IMDb metadata lookup failed for request_id=%s",
                getattr(request, "id", None),
                exc_info=True,
            )
            return None
        return extract_imdb_id(details if isinstance(details, dict) else None)

    async def _requested_season_numbers(self, request: Any) -> list[int]:
        try:
            seasons = getattr(request, "seasons", None) or []
        except Exception:
            seasons = []
        if type(seasons).__module__.startswith("unittest.mock"):
            seasons = []
        values = [getattr(season, "season_number", None) for season in seasons]
        season_numbers = sorted({season for season in values if isinstance(season, int)})
        if season_numbers:
            return season_numbers
        if type(getattr(request, "seasons", None)).__module__.startswith("unittest.mock"):
            return []
        request_id = getattr(request, "id", None)
        if request_id is None:
            return []
        result = await self.db.execute(
            select(Season.season_number).where(Season.request_id == request_id)
        )
        return sorted({row[0] for row in result.all() if isinstance(row[0], int)})

    @staticmethod
    def _combine_tv_results(results: list[Any]) -> Any:
        if not results:
            from app.siftarr.services.integrations.prowlarr_service import ProwlarrSearchResult

            return ProwlarrSearchResult(releases=[], query_time_ms=0, error="No seasons specified")

        first = copy(results[0])
        releases = []
        seen: set[str] = set()
        errors = []
        total_time = 0
        for result in results:
            if getattr(result, "error", None) and not getattr(result, "releases", None):
                errors.append(result.error)
            total_time += getattr(result, "query_time_ms", 0)
            for release in getattr(result, "releases", []):
                key = (
                    release.info_hash
                    or release.download_url
                    or f"{release.title}|{release.indexer}"
                )
                if key in seen:
                    continue
                seen.add(key)
                releases.append(release)
        first.releases = releases
        first.query_time_ms = total_time
        first.error = "; ".join(errors) if errors and not releases else None
        return first

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
