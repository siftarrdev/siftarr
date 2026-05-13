"""TV decision service — episode-centric status model.

Episode status is the sole ground truth.  Season and Request statuses are
derived upward from episodes and written as a cached summary after any
episode mutation.

Workflow (Parallel Search):
1. Search for season packs AND individual episodes in parallel
2. Evaluate all releases through RuleEngine
3. Prefer season packs over episode releases when both pass
4. Send best matches to qBit (or staging)
5. Update **only** Episode statuses — season/request statuses are derived
6. If nothing passes → add to pending queue
"""

import asyncio
import logging
from collections import Counter
from collections.abc import Sequence
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.siftarr.config import get_settings
from app.siftarr.models.episode import Episode
from app.siftarr.models.release import Release
from app.siftarr.models.request import MediaType, Request, RequestStatus
from app.siftarr.models.season import Season
from app.siftarr.models.staged_torrent import StagedTorrent
from app.siftarr.services.decisions.decision_pipeline import (
    add_to_pending_queue,
    build_rule_engine,
    log_release_staged,
    log_rule_evaluation,
)
from app.siftarr.services.decisions.rule_engine import (
    ReleaseEvaluation,
    RuleEngine,
    get_cached_engine,
    set_cached_engine,
)
from app.siftarr.services.integrations.overseerr_service import OverseerrService
from app.siftarr.services.integrations.prowlarr_service import (
    ProwlarrRelease,
    ProwlarrSearchResult,
    ProwlarrService,
)
from app.siftarr.services.integrations.qbittorrent_service import QbittorrentService
from app.siftarr.services.lifecycle.episode_derive import (
    derive_request_status_from_episodes,
    derive_season_status,
)
from app.siftarr.services.metadata_service import extract_imdb_id
from app.siftarr.services.releases.release_parser import (
    cached_parse_release_coverage,
    is_exact_single_episode_release,
)
from app.siftarr.services.releases.release_storage import (
    get_release_persistence_key,
    store_search_results,
)
from app.siftarr.services.releases.staging_service import StagingService
from app.siftarr.services.stats_metrics_service import record_rule_outcomes

logger = logging.getLogger(__name__)

MAX_CONCURRENT_SEARCHES = 5
ACTIONABLE_EXCLUDED_STATUSES = {
    RequestStatus.COMPLETED,
    RequestStatus.DOWNLOADING,
    RequestStatus.STAGED,
}


class TVDecisionService:
    """
    Service for making download decisions for TV requests.

    Episode status is the authoritative state.  Season and Request statuses
    are derived upward from episodes.
    """

    def __init__(
        self,
        db: AsyncSession,
        prowlarr: ProwlarrService,
        qbittorrent: QbittorrentService,
    ):
        self.db: AsyncSession = db
        self.prowlarr = prowlarr
        self.qbittorrent = qbittorrent
        self._settings = get_settings()

    async def _get_rule_engine(self) -> RuleEngine:
        """Get configured rule engine from database rules (cached per media type)."""
        media_type = MediaType.TV.value
        cached = get_cached_engine(media_type)
        if cached is not None:
            return cached

        engine = await build_rule_engine(self.db, media_type)
        set_cached_engine(media_type, engine)
        return engine

    def _get_requested_seasons(self, request: Request) -> list[int]:
        return sorted([s.season_number for s in request.seasons])

    def _get_requested_episodes(self, request: Request) -> dict[int, list[int]]:
        return {
            s.season_number: [e.episode_number for e in s.episodes]
            for s in request.seasons
            if s.episodes
        }

    @staticmethod
    def _episode_is_actionable(episode: Episode) -> bool:
        status = getattr(episode, "status", RequestStatus.PENDING)
        if status in ACTIONABLE_EXCLUDED_STATUSES:
            return False
        air_date = getattr(episode, "air_date", None)
        return not isinstance(air_date, date) or air_date <= date.today()

    def _get_actionable_targets(self, request: Request) -> tuple[list[int], dict[int, list[int]]]:
        seasons: list[int] = []
        episodes_by_season: dict[int, list[int]] = {}
        for season in request.seasons:
            season_number = season.season_number
            episodes = list(getattr(season, "episodes", []) or [])
            if episodes:
                actionable = sorted(
                    ep.episode_number for ep in episodes if self._episode_is_actionable(ep)
                )
                if not actionable:
                    continue
                episodes_by_season[season_number] = actionable
            seasons.append(season_number)
        return sorted(seasons), episodes_by_season

    @staticmethod
    def _get_sweep_seasons(
        requested_seasons: Sequence[int], requested_episodes: dict[int, list[int]]
    ) -> list[int]:
        """Search only seasons that have episode targets, unless this is pack-only scope."""
        if not requested_episodes:
            return list(requested_seasons)
        return [season for season in requested_seasons if requested_episodes.get(season)]

    async def _bounded_searches(
        self, searches: Sequence[tuple[str, int | None, int | None]], request: Request
    ) -> list[object]:
        semaphore = asyncio.Semaphore(MAX_CONCURRENT_SEARCHES)
        assert request.tvdb_id is not None
        tvdb_id = request.tvdb_id

        async def run_search(search_type: str, season: int | None, episode: int | None) -> object:
            async with semaphore:
                return await self.prowlarr.search_by_tvdbid(
                    tvdbid=tvdb_id,
                    title=request.title,
                    season=season,
                    episode=episode,
                    year=request.year,
                )

        return await asyncio.gather(
            *(
                run_search(search_type, season, episode)
                for search_type, season, episode in searches
            ),
            return_exceptions=True,
        )

    @staticmethod
    def _get_pack_coverage(evaluation: ReleaseEvaluation, requested_seasons: set[int]) -> set[int]:
        coverage = cached_parse_release_coverage(evaluation.release.title)
        if coverage.episode_number is not None:
            return set()

        if coverage.is_complete_series:
            return set(requested_seasons)

        return set(coverage.season_numbers).intersection(requested_seasons)

    @staticmethod
    def _get_actionable_pack_coverage(
        evaluation: ReleaseEvaluation,
        actionable_seasons: set[int],
        all_requested_seasons: set[int],
    ) -> set[int]:
        """Return pack coverage only when the torrent avoids completed requested seasons."""
        coverage = cached_parse_release_coverage(evaluation.release.title)
        if coverage.episode_number is not None:
            return set()

        if coverage.is_complete_series:
            if all_requested_seasons - actionable_seasons:
                return set()
            return set(actionable_seasons)

        covered_requested_seasons = set(coverage.season_numbers).intersection(all_requested_seasons)
        if not covered_requested_seasons:
            return set()
        if not covered_requested_seasons <= actionable_seasons:
            return set()
        return covered_requested_seasons

    @staticmethod
    def _get_multi_season_coverage(
        evaluation: ReleaseEvaluation, requested_seasons: set[int]
    ) -> set[int]:
        """Return only multi-season/complete-series coverage for compatibility."""
        coverage = TVDecisionService._get_pack_coverage(evaluation, requested_seasons)
        return coverage if len(coverage) > 1 else set()

    @staticmethod
    def _is_exact_season_pack(evaluation: ReleaseEvaluation, requested_season: int) -> bool:
        coverage = cached_parse_release_coverage(evaluation.release.title)
        return (
            coverage.episode_number is None
            and not coverage.is_complete_series
            and coverage.season_numbers == (requested_season,)
        )

    @staticmethod
    def _is_exact_episode_match(
        evaluation: ReleaseEvaluation, season_number: int, episode_number: int
    ) -> bool:
        return is_exact_single_episode_release(
            evaluation.release.title,
            season_number,
            episode_number,
        )

    async def _get_aired_db_episodes_for_season(
        self, request_id: int, season_number: int
    ) -> list[int]:
        result = await self.db.execute(
            select(Episode.episode_number)
            .join(Season, Episode.season_id == Season.id)
            .where(
                Season.request_id == request_id,
                Season.season_number == season_number,
                Episode.air_date.is_not(None),
                Episode.air_date <= date.today(),
            )
            .order_by(Episode.episode_number)
        )
        return [row[0] for row in result.all()]

    async def _get_unresolved_aired_db_episodes_for_season(
        self, request_id: int, season_number: int
    ) -> list[int]:
        result = await self.db.execute(
            select(Episode.episode_number)
            .join(Season, Episode.season_id == Season.id)
            .where(
                Season.request_id == request_id,
                Season.season_number == season_number,
                Episode.air_date.is_not(None),
                Episode.air_date <= date.today(),
                Episode.status.not_in(tuple(ACTIONABLE_EXCLUDED_STATUSES)),
            )
            .order_by(Episode.episode_number)
        )
        return [row[0] for row in result.all()]

    async def _get_episode_search_targets(
        self,
        request: Request,
        season_number: int,
        requested_episodes: dict[int, list[int]],
    ) -> list[int]:
        explicit_episodes = requested_episodes.get(season_number, [])
        if explicit_episodes:
            return explicit_episodes

        aired_episodes = await self._get_unresolved_aired_db_episodes_for_season(
            request.id, season_number
        )
        return aired_episodes[: self._settings.max_episode_discovery]

    async def _search_and_evaluate(
        self,
        request: Request,
        rule_engine: RuleEngine,
        searches: Sequence[tuple[str, int | None, int | None]],
    ) -> tuple[
        list[ReleaseEvaluation], list[tuple[int | None, int | None, ReleaseEvaluation]], list[str]
    ]:
        if not searches:
            return [], [], []
        assert request.tvdb_id is not None

        search_results = await self._bounded_searches(searches, request)

        evaluated_releases: list[ReleaseEvaluation] = []
        passing_releases: list[tuple[int | None, int | None, ReleaseEvaluation]] = []
        errors: list[str] = []

        # Dedup set keyed across all search results
        seen_keys: set[str] = set()

        for (search_type, season, episode), search_result in zip(
            searches, search_results, strict=False
        ):
            if isinstance(search_result, Exception):
                logger.warning(
                    "TV search failed: request_id=%s type=%s season=%s episode=%s error=%s",
                    request.id,
                    search_type,
                    season,
                    episode,
                    search_result,
                )
                errors.append(str(search_result))
                continue
            if not isinstance(search_result, ProwlarrSearchResult):
                errors.append("Unexpected search result type")
                continue
            if search_result.error:
                logger.warning(
                    "TV search error: request_id=%s type=%s season=%s episode=%s error=%s",
                    request.id,
                    search_type,
                    season,
                    episode,
                    search_result.error,
                )
                errors.append(search_result.error)
                continue

            for release in search_result.releases:
                # Dedup across broad / season / episode searches
                dedup_key = self._release_dedup_key(release)
                if dedup_key in seen_keys:
                    continue
                seen_keys.add(dedup_key)

                evaluation = rule_engine.evaluate(release)
                evaluated_releases.append(evaluation)
                if evaluation.passed:
                    passing_releases.append((season, episode, evaluation))

        return evaluated_releases, passing_releases, errors

    async def _search_season_sweeps_and_evaluate(
        self,
        request: Request,
        rule_engine: RuleEngine,
        seasons: Sequence[int],
    ) -> tuple[list[ReleaseEvaluation], list[ReleaseEvaluation], list[str], set[int]]:
        """Run one logical paginated season sweep per requested season and dedupe results."""
        if not seasons:
            return [], [], [], set()

        imdb_id = await self._load_imdb_id(request)
        logger.info(
            "TV Search All season sweeps started: request_id=%s title=%s seasons=%s source=prowlarr",
            request.id,
            request.title,
            list(seasons),
        )
        search_results = await asyncio.gather(
            *(
                self.prowlarr.search_tv_season_sweep(
                    title=request.title,
                    season=season,
                    imdbid=imdb_id,
                    tvdbid=request.tvdb_id,
                    request_id=request.id,
                )
                for season in seasons
            ),
            return_exceptions=True,
        )

        evaluated_releases: list[ReleaseEvaluation] = []
        passing_releases: list[ReleaseEvaluation] = []
        errors: list[str] = []
        seen_keys: set[str] = set()
        limited_seasons: set[int] = set()

        for season, search_result in zip(seasons, search_results, strict=False):
            if isinstance(search_result, Exception):
                logger.warning(
                    "TV season sweep failed: request_id=%s season=%s error=%s",
                    request.id,
                    season,
                    search_result,
                )
                errors.append(str(search_result))
                continue
            if not isinstance(search_result, ProwlarrSearchResult):
                errors.append("Unexpected search result type")
                continue
            if search_result.error and not search_result.releases:
                logger.warning(
                    "TV season sweep error: request_id=%s season=%s error=%s",
                    request.id,
                    season,
                    search_result.error,
                )
                errors.append(search_result.error)

            logger.info(
                "TV Search All season sweep loaded: request_id=%s title=%s season=%s count=%s page_count=%s hit_limit=%s source=%s",
                request.id,
                request.title,
                season,
                len(search_result.releases),
                search_result.page_count,
                search_result.hit_limit,
                search_result.source or "prowlarr",
            )

            if search_result.hit_limit:
                limited_seasons.add(season)

            for release in search_result.releases:
                dedup_key = self._release_dedup_key(release)
                if dedup_key in seen_keys:
                    continue
                seen_keys.add(dedup_key)

                evaluation = rule_engine.evaluate(release)
                evaluated_releases.append(evaluation)
                if evaluation.passed:
                    passing_releases.append(evaluation)

        return evaluated_releases, passing_releases, errors, limited_seasons

    async def _search_exact_episode_fallbacks_and_evaluate(
        self,
        request: Request,
        rule_engine: RuleEngine,
        targets: Sequence[tuple[int, int]],
        seen_keys: set[str],
    ) -> tuple[list[ReleaseEvaluation], list[tuple[int, int, ReleaseEvaluation]], list[str]]:
        if not targets:
            return [], [], []
        assert request.tvdb_id is not None

        searches = [("episode", season, episode) for season, episode in targets]
        search_results = await self._bounded_searches(searches, request)

        evaluated_releases: list[ReleaseEvaluation] = []
        passing_releases: list[tuple[int, int, ReleaseEvaluation]] = []
        errors: list[str] = []

        for (_, season, episode), search_result in zip(searches, search_results, strict=False):
            if isinstance(search_result, Exception):
                logger.warning(
                    "TV exact episode fallback failed: request_id=%s season=%s episode=%s error=%s",
                    request.id,
                    season,
                    episode,
                    search_result,
                )
                errors.append(str(search_result))
                continue
            if not isinstance(search_result, ProwlarrSearchResult):
                errors.append("Unexpected search result type")
                continue
            if search_result.error and not search_result.releases:
                errors.append(search_result.error)
                continue

            for release in search_result.releases:
                dedup_key = self._release_dedup_key(release)
                if dedup_key in seen_keys:
                    continue
                seen_keys.add(dedup_key)

                evaluation = rule_engine.evaluate(release)
                evaluated_releases.append(evaluation)
                if evaluation.passed and self._is_exact_episode_match(evaluation, season, episode):
                    passing_releases.append((season, episode, evaluation))

        return evaluated_releases, passing_releases, errors

    async def _load_imdb_id(self, request: Request) -> str | None:
        if not request.tmdb_id:
            return None
        try:
            details = await OverseerrService(settings=self._settings).get_media_details(
                "tv", request.tmdb_id
            )
        except Exception:
            logger.warning(
                "IMDb metadata lookup failed for request_id=%s", request.id, exc_info=True
            )
            return None
        return extract_imdb_id(details if isinstance(details, dict) else None)

    @staticmethod
    def _release_dedup_key(release: ProwlarrRelease) -> str:
        """Compute a deduplication key for a Prowlarr release."""
        if release.info_hash:
            return f"ih:{release.info_hash.lower()}"
        if release.download_url:
            return f"url:{release.download_url.lower()}"
        return f"t:{release.title.lower()}|i:{release.indexer.lower()}"

    @staticmethod
    def _merge_evaluations_for_storage(
        base: list[ReleaseEvaluation], additions: list[ReleaseEvaluation]
    ) -> list[ReleaseEvaluation]:
        """Append fallback evaluations without replacing prior sweep rows."""
        merged = list(base)
        seen = {TVDecisionService._release_dedup_key(evaluation.release) for evaluation in merged}
        for evaluation in additions:
            dedup_key = TVDecisionService._release_dedup_key(evaluation.release)
            if dedup_key in seen:
                continue
            seen.add(dedup_key)
            merged.append(evaluation)
        return merged

    @staticmethod
    def _exact_episode_bucket_counts(
        evaluations: Sequence[ReleaseEvaluation], requested_seasons: Sequence[int]
    ) -> dict[int, int]:
        counts: Counter[int] = Counter()
        requested_season_set = set(requested_seasons)
        for evaluation in evaluations:
            coverage = cached_parse_release_coverage(evaluation.release.title)
            season = coverage.season_number
            episode = coverage.episode_number
            if season is None or episode is None or season not in requested_season_set:
                continue
            if is_exact_single_episode_release(evaluation.release.title, season, episode):
                counts[season] += 1
        return {season: counts.get(season, 0) for season in requested_seasons}

    async def _set_episode_status(
        self, request_id: int, season_number: int, episode_number: int, status: RequestStatus
    ) -> None:
        """Set a single episode's status."""
        result = await self.db.execute(
            select(Episode)
            .join(Season, Episode.season_id == Season.id)
            .where(
                Season.request_id == request_id,
                Season.season_number == season_number,
                Episode.episode_number == episode_number,
            )
        )
        episode = result.scalar_one_or_none()
        if episode:
            episode.status = status

    async def _set_episodes_for_season(
        self, request_id: int, season_number: int, status: RequestStatus
    ) -> None:
        """Set all **aired** episodes in a season to the given status."""
        aired = await self._get_unresolved_aired_db_episodes_for_season(request_id, season_number)
        for ep_num in aired:
            await self._set_episode_status(request_id, season_number, ep_num, status)

    async def _recompute_tv_statuses(self, request: Request) -> None:
        """Recompute Season.status and Request.status from episode ground truth."""
        all_episodes: list[Episode] = []
        for season in request.seasons:
            season_episodes = list(season.episodes)
            season.status = derive_season_status(season_episodes)
            all_episodes.extend(season_episodes)

        request.status = derive_request_status_from_episodes(all_episodes)

    async def process_request(self, request_id: int, search_episodes: bool = True) -> dict:
        """
        Process a TV request search.

        Args:
            request_id: The ID of the request to search.
            search_episodes: If True (default), also search for individual episodes
                for uncovered seasons. When False, only season packs and multi-season
                packs are searched.
        """
        result = await self.db.execute(
            select(Request)
            .where(Request.id == request_id)
            .options(selectinload(Request.seasons).selectinload(Season.episodes))
        )
        request = result.scalar_one_or_none()

        if not request:
            return {"status": "error", "message": "Request not found"}

        if request.media_type != MediaType.TV:
            return {"status": "error", "message": "Request is not TV type"}

        # Log search start but don't set Request.status — it's derived from episodes
        logger.info(
            "TV search started: request_id=%s title=%s tvdb_id=%s",
            request.id,
            request.title,
            request.tvdb_id,
        )

        all_requested_seasons = set(self._get_requested_seasons(request))
        requested_seasons, requested_episodes = self._get_actionable_targets(request)
        requested_seasons = self._get_sweep_seasons(requested_seasons, requested_episodes)

        logger.info(
            "TV search parsed request: request_id=%s seasons=%s episodes_by_season=%s",
            request.id,
            requested_seasons,
            requested_episodes,
        )

        rule_engine = await self._get_rule_engine()

        if request.tvdb_id is None:
            logger.warning("TV request %s has no TVDB ID", request_id)
            return {"status": "error", "message": "No TVDB ID available for TV show"}

        if not self._get_requested_seasons(request):
            return {"status": "error", "message": "No seasons specified"}
        if not requested_seasons:
            await self._recompute_tv_statuses(request)
            await self.db.commit()
            return {
                "status": "pending",
                "selected_releases": [],
                "message": "No unresolved aired episodes remain.",
            }

        all_evaluated_releases: list[ReleaseEvaluation] = []
        all_search_errors: list[str] = []
        fallback_target_count = 0
        fallback_release_count = 0
        passing_pack_count = 0
        episode_evaluations: list[tuple[int, int, ReleaseEvaluation]] = []

        all_selected_releases: list[ReleaseEvaluation] = []
        selected_pack_releases: list[tuple[ReleaseEvaluation, set[int]]] = []
        covered_seasons: set[int] = set()

        # ── One logical paginated season sweep per requested season ───
        (
            sweep_evaluations,
            sweep_candidates,
            sweep_errors,
            limited_sweep_seasons,
        ) = await self._search_season_sweeps_and_evaluate(request, rule_engine, requested_seasons)
        all_evaluated_releases.extend(sweep_evaluations)
        all_search_errors.extend(sweep_errors)

        requested_season_set = set(requested_seasons)

        # ── Pack-first selection from classified sweep results ────────
        pack_candidates: list[tuple[ReleaseEvaluation, set[int]]] = []
        for evaluation in sweep_candidates:
            coverage = self._get_actionable_pack_coverage(
                evaluation,
                requested_season_set,
                all_requested_seasons,
            )
            if not coverage:
                continue
            passing_pack_count += 1
            pack_candidates.append((evaluation, coverage))

        for evaluation, coverage in sorted(
            pack_candidates, key=lambda item: (len(item[1]), item[0].total_score), reverse=True
        ):
            uncovered_coverage = coverage - covered_seasons
            if not uncovered_coverage:
                continue
            selected_pack_releases.append((evaluation, uncovered_coverage))
            covered_seasons.update(uncovered_coverage)
            all_selected_releases.append(evaluation)
            if covered_seasons >= requested_season_set:
                break

        # ── Exact episode fallback from stored/classified sweep results ─
        best_episodes_by_key: dict[tuple[int, int], ReleaseEvaluation] = {}
        uncovered_episode_target_keys: set[tuple[int, int]] = set()
        if search_episodes:
            episode_targets: dict[int, list[int]] = {}
            for season in requested_seasons:
                if season in covered_seasons:
                    continue
                episode_targets[season] = await self._get_episode_search_targets(
                    request, season, requested_episodes
                )
            uncovered_episode_target_keys = {
                (season, episode)
                for season, episodes in episode_targets.items()
                for episode in episodes
            }

            for evaluation in sweep_candidates:
                for season, episodes in episode_targets.items():
                    for episode in episodes:
                        if not self._is_exact_episode_match(evaluation, season, episode):
                            continue
                        episode_evaluations.append((season, episode, evaluation))
                        key = (season, episode)
                        existing = best_episodes_by_key.get(key)
                        if existing is None or evaluation.total_score > existing.total_score:
                            best_episodes_by_key[key] = evaluation

            selected_sweep_episode_keys = set(best_episodes_by_key)
            capped_missing_targets = [
                (season, episode)
                for season, episodes in episode_targets.items()
                if season in limited_sweep_seasons
                for episode in episodes
                if (season, episode) not in selected_sweep_episode_keys
            ]
            if capped_missing_targets:
                fallback_target_count = len(capped_missing_targets)
                logger.info(
                    "TV exact episode fallbacks started: request_id=%s targets=%s source=prowlarr",
                    request.id,
                    capped_missing_targets,
                )
                seen_keys = {
                    self._release_dedup_key(evaluation.release)
                    for evaluation in all_evaluated_releases
                }
                (
                    fallback_evaluations,
                    fallback_candidates,
                    fallback_errors,
                ) = await self._search_exact_episode_fallbacks_and_evaluate(
                    request,
                    rule_engine,
                    capped_missing_targets,
                    seen_keys,
                )
                fallback_release_count = len(fallback_evaluations)
                all_evaluated_releases = self._merge_evaluations_for_storage(
                    all_evaluated_releases,
                    fallback_evaluations,
                )
                all_search_errors.extend(fallback_errors)
                for season, episode, evaluation in fallback_candidates:
                    episode_evaluations.append((season, episode, evaluation))
                    key = (season, episode)
                    existing = best_episodes_by_key.get(key)
                    if existing is None or evaluation.total_score > existing.total_score:
                        best_episodes_by_key[key] = evaluation

            all_selected_releases.extend(best_episodes_by_key.values())

        selected_episode_keys = set(best_episodes_by_key)
        logger.info(
            "TV selected episode coverage summary: request_id=%s requested_uncovered_episode_keys=%s selected_episode_keys=%s missing_episode_keys=%s covered_seasons=%s selected_pack_count=%s",
            request.id,
            sorted(uncovered_episode_target_keys),
            sorted(selected_episode_keys),
            sorted(uncovered_episode_target_keys - selected_episode_keys),
            sorted(covered_seasons),
            len(selected_pack_releases),
        )

        logger.info(
            "TV exact episode fallback merge summary: request_id=%s fallback_target_count=%s fallback_release_count=%s final_result_count=%s exact_episode_bucket_counts=%s source=prowlarr",
            request.id,
            fallback_target_count,
            fallback_release_count,
            len(all_evaluated_releases),
            self._exact_episode_bucket_counts(all_evaluated_releases, requested_seasons),
        )

        logger.info(
            "TV search completed: request_id=%s title=%s total_results=%s passing_packs=%s passing_episodes=%s errors=%s source=prowlarr",
            request.id,
            request.title,
            len(all_evaluated_releases),
            passing_pack_count,
            len(episode_evaluations),
            len(all_search_errors),
        )

        await log_rule_evaluation(
            self.db,
            request_id=request_id,
            evaluated=len(all_evaluated_releases),
            passed_packs=passing_pack_count,
            passed_episodes=len(episode_evaluations),
            search_errors=len(all_search_errors),
        )

        stored_releases_by_key = await store_search_results(
            self.db,
            request.id,
            all_evaluated_releases,
        )
        await record_rule_outcomes(
            self.db,
            request_id=request.id,
            evaluations=all_evaluated_releases,
            stored_releases_by_key=stored_releases_by_key,
        )
        await self.db.commit()

        if all_selected_releases:
            stored_releases: list[Release] = []
            seen_selected_keys: set[str] = set()
            for evaluation in all_selected_releases:
                selected_key = get_release_persistence_key(
                    title=evaluation.release.title,
                    info_hash=evaluation.release.info_hash,
                )
                if selected_key in seen_selected_keys:
                    continue
                seen_selected_keys.add(selected_key)

                stored_release = stored_releases_by_key.get(selected_key)
                if stored_release is None:
                    logger.warning(
                        "Selected TV release missing after persistence: request_id=%s title=%s info_hash=%s",
                        request.id,
                        evaluation.release.title,
                        evaluation.release.info_hash,
                    )
                    continue
                stored_releases.append(stored_release)

            logger.info(
                "TV selected releases: request_id=%s count=%s releases=%s",
                request.id,
                len(all_selected_releases),
                [e.release.title for e in all_selected_releases],
            )

            action_result = await StagingService(self.db).use_releases(
                request,
                stored_releases,
                selection_source="rule",
            )

            await log_release_staged(
                self.db,
                request_id=request_id,
                release_count=len(all_selected_releases),
                titles=[e.release.title for e in all_selected_releases[:5]],
                action=action_result.get("status"),
            )

            # ── Episode status updates ────────────────────────────────
            # Episode status is the ground truth.  Set each episode covered
            # by the selected releases, then derive season/request statuses.
            if action_result.get("status") in ("completed", "downloading", "staged"):
                status_map = {
                    "completed": RequestStatus.COMPLETED,
                    "downloading": RequestStatus.DOWNLOADING,
                    "staged": RequestStatus.STAGED,
                }
                action_status: str = str(action_result.get("status", ""))
                new_status = status_map[action_status]

                # Set episode statuses from pack releases
                for _, covered_seasons_set in selected_pack_releases:
                    for season_num in covered_seasons_set:
                        await self._set_episodes_for_season(request.id, season_num, new_status)

                # Set episode statuses from individual episode releases
                for season, episode in best_episodes_by_key:
                    await self._set_episode_status(request.id, season, episode, new_status)

                # Recompute season and request statuses from episode ground truth
                await self.db.flush()
                # Reload with episodes for recomputation
                reload_result = await self.db.execute(
                    select(Request)
                    .where(Request.id == request.id)
                    .options(selectinload(Request.seasons).selectinload(Season.episodes))
                )
                reloaded = reload_result.scalar_one_or_none()
                if reloaded:
                    await self._recompute_tv_statuses(reloaded)
                await self.db.flush()

            return {
                "status": action_result["status"],
                "selected_releases": [
                    {
                        "title": e.release.title,
                        "score": e.total_score,
                        "download_url": e.release.download_url,
                        "magnet_url": e.release.magnet_url,
                    }
                    for e in all_selected_releases
                ],
                "message": action_result["message"],
            }

        # ── No releases passed ────────────────────────────────────────
        active_stage_result = await self.db.execute(
            select(StagedTorrent).where(
                StagedTorrent.request_id == request.id,
                StagedTorrent.status.in_(("staged", "approved")),
            )
        )
        active_stages = list(active_stage_result.scalars().all())
        if active_stages:
            # Derive request status from episodes (stage may already be reflected)
            await self._recompute_tv_statuses(request)
            await self.db.commit()
            return {
                "status": "staged",
                "selected_releases": [],
                "message": "Active staged selection preserved.",
            }

        # No releases passed and no active stages — derive PENDING
        await self._recompute_tv_statuses(request)
        await self.db.commit()

        rejection_reasons = []
        for e in all_evaluated_releases:
            if e.rejection_reason:
                rejection_reasons.append(e.rejection_reason)

        all_errors = list(set(all_search_errors))
        error_msg = (
            "; ".join(set(rejection_reasons))[:500]
            if rejection_reasons
            else "All releases rejected by rules"
        )
        if all_errors:
            error_msg = f"Search errors: {'; '.join(all_errors)[:200]}. {error_msg}"

        logger.info(
            "TV search rejected all releases: request_id=%s evaluated=%s rejection_reasons=%s search_errors=%s",
            request.id,
            len(all_evaluated_releases),
            list(set(rejection_reasons))[:5],
            len(all_errors),
        )

        await add_to_pending_queue(
            self.db,
            request.id,
            error_message=error_msg,
        )

        return {
            "status": "pending",
            "message": f"No releases passed rules. {len(all_evaluated_releases)} releases evaluated.",
            "rejection_reasons": list(set(rejection_reasons))[:5],
            "search_errors": all_errors,
        }
