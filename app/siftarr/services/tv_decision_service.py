import asyncio
import logging
from collections.abc import Sequence
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.siftarr.config import get_settings
from app.siftarr.models.episode import Episode
from app.siftarr.models.release import Release
from app.siftarr.models.request import MediaType, Request, RequestStatus
from app.siftarr.models.rule import Rule
from app.siftarr.models.season import Season
from app.siftarr.services.activity_log_service import ActivityLogService
from app.siftarr.services.pending_queue_service import PendingQueueService
from app.siftarr.services.prowlarr_service import ProwlarrSearchResult, ProwlarrService
from app.siftarr.services.qbittorrent_service import QbittorrentService
from app.siftarr.services.release_parser import (
    is_exact_single_episode_release,
    parse_release_coverage,
)
from app.siftarr.services.release_storage import get_release_persistence_key, store_search_results
from app.siftarr.services.rule_engine import ReleaseEvaluation, RuleEngine
from app.siftarr.services.staging_actions import use_releases

logger = logging.getLogger(__name__)

MAX_CONCURRENT_SEARCHES = 5


class TVDecisionService:
    """
    Service for making download decisions for TV requests.

    Workflow (Parallel Search):
    1. Search for season packs AND individual episodes in parallel
    2. Evaluate all releases through RuleEngine
    3. Prefer season packs over episode releases when both pass
    4. Send best matches to qBit (or staging)
    5. Update Episode statuses accordingly
    6. If nothing passes → add to pending queue
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
        result = await self.db.execute(select(Rule))
        rules = list(result.scalars().all())
        return RuleEngine.from_db_rules(rules=rules, media_type=MediaType.TV.value)

    def _get_requested_seasons(self, request: Request) -> list[int]:
        return sorted([s.season_number for s in request.seasons])

    def _get_requested_episodes(self, request: Request) -> dict[int, list[int]]:
        return {
            s.season_number: [e.episode_number for e in s.episodes]
            for s in request.seasons
            if s.episodes
        }

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
    def _get_multi_season_coverage(
        evaluation: ReleaseEvaluation, requested_seasons: set[int]
    ) -> set[int]:
        coverage = parse_release_coverage(evaluation.release.title)
        if coverage.episode_number is not None:
            return set()

        if coverage.is_complete_series:
            return set(requested_seasons)

        season_coverage = set(coverage.season_numbers).intersection(requested_seasons)
        if len(season_coverage) < 2:
            return set()
        return season_coverage

    @staticmethod
    def _is_exact_season_pack(evaluation: ReleaseEvaluation, requested_season: int) -> bool:
        coverage = parse_release_coverage(evaluation.release.title)
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

    async def _get_episode_search_targets(
        self,
        request: Request,
        season_number: int,
        requested_episodes: dict[int, list[int]],
    ) -> list[int]:
        explicit_episodes = requested_episodes.get(season_number, [])
        if explicit_episodes:
            return explicit_episodes

        aired_episodes = await self._get_aired_db_episodes_for_season(request.id, season_number)
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
                evaluation = rule_engine.evaluate(release)
                evaluated_releases.append(evaluation)
                if evaluation.passed:
                    passing_releases.append((season, episode, evaluation))

        return evaluated_releases, passing_releases, errors

    async def _update_episode_status(
        self, request_id: int, season_number: int, episode_number: int | None, status: RequestStatus
    ) -> None:
        if episode_number is not None:
            result = await self.db.execute(
                select(Episode)
                .join(Season, Episode.season_id == Season.id)
                .where(
                    Season.request_id == request_id,
                    Season.season_number == season_number,
                    Episode.episode_number == episode_number,
                )
            )
        else:
            result = await self.db.execute(
                select(Episode)
                .join(Season, Episode.season_id == Season.id)
                .where(Season.request_id == request_id, Season.season_number == season_number)
            )
        episodes = list(result.scalars().all())
        for ep in episodes:
            ep.status = status
        if episodes:
            await self.db.flush()

    async def _update_season_status(
        self, request_id: int, season_number: int, status: RequestStatus
    ) -> None:
        result = await self.db.execute(
            select(Season).where(
                Season.request_id == request_id, Season.season_number == season_number
            )
        )
        season = result.scalar_one_or_none()
        if season:
            season.status = status
            await self.db.flush()

    async def process_request(self, request_id: int) -> dict:
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

        request.status = RequestStatus.SEARCHING
        await self.db.commit()

        requested_seasons = self._get_requested_seasons(request)
        requested_episodes = self._get_requested_episodes(request)

        logger.info(
            "TV search started: request_id=%s title=%s tvdb_id=%s seasons=%s episodes=%s",
            request.id,
            request.title,
            request.tvdb_id,
            requested_seasons,
            requested_episodes,
        )

        rule_engine = await self._get_rule_engine()

        if request.tvdb_id is None:
            request.status = RequestStatus.FAILED
            await self.db.commit()
            return {"status": "error", "message": "No TVDB ID available for TV show"}

        logger.info(
            "TV search parsed request: request_id=%s seasons=%s episodes_by_season=%s",
            request.id,
            requested_seasons,
            requested_episodes,
        )

        if not requested_seasons:
            return {"status": "error", "message": "No seasons specified"}

        all_evaluated_releases: list[ReleaseEvaluation] = []
        all_search_errors: list[str] = []
        passing_pack_count = 0
        episode_evaluations: list[tuple[int, int, ReleaseEvaluation]] = []

        all_selected_releases: list[ReleaseEvaluation] = []
        selected_pack_releases: list[tuple[ReleaseEvaluation, set[int]]] = []
        covered_seasons: set[int] = set()

        if len(requested_seasons) > 1:
            broad_evaluations, broad_candidates, broad_errors = await self._search_and_evaluate(
                request,
                rule_engine,
                [("multi_season_pack", None, None)],
            )
            all_evaluated_releases.extend(broad_evaluations)
            all_search_errors.extend(broad_errors)

            best_multi_season_pack: tuple[ReleaseEvaluation, set[int]] | None = None
            requested_season_set = set(requested_seasons)
            for _, _, evaluation in broad_candidates:
                coverage = self._get_multi_season_coverage(evaluation, requested_season_set)
                if not coverage:
                    continue
                passing_pack_count += 1
                if best_multi_season_pack is None or (
                    len(coverage),
                    evaluation.total_score,
                ) > (
                    len(best_multi_season_pack[1]),
                    best_multi_season_pack[0].total_score,
                ):
                    best_multi_season_pack = (evaluation, coverage)

            if best_multi_season_pack is not None:
                selected_pack_releases.append(best_multi_season_pack)
                covered_seasons.update(best_multi_season_pack[1])
                all_selected_releases.append(best_multi_season_pack[0])

        uncovered_seasons = [
            season for season in requested_seasons if season not in covered_seasons
        ]
        season_pack_searches = [("season_pack", season, None) for season in uncovered_seasons]
        season_evaluations, season_candidates, season_errors = await self._search_and_evaluate(
            request,
            rule_engine,
            season_pack_searches,
        )
        all_evaluated_releases.extend(season_evaluations)
        all_search_errors.extend(season_errors)

        best_season_packs: dict[int, ReleaseEvaluation] = {}
        for season, _, evaluation in season_candidates:
            assert season is not None
            if not self._is_exact_season_pack(evaluation, season):
                continue
            passing_pack_count += 1
            existing = best_season_packs.get(season)
            if existing is None or evaluation.total_score > existing.total_score:
                best_season_packs[season] = evaluation

        for season in uncovered_seasons:
            evaluation = best_season_packs.get(season)
            if evaluation is None:
                continue
            selected_pack_releases.append((evaluation, {season}))
            covered_seasons.add(season)
            all_selected_releases.append(evaluation)

        episode_searches: list[tuple[str, int | None, int | None]] = []
        for season in requested_seasons:
            if season in covered_seasons:
                continue
            for episode in await self._get_episode_search_targets(
                request, season, requested_episodes
            ):
                episode_searches.append(("episode", season, episode))

        (
            episode_stage_evaluations,
            episode_candidates,
            episode_errors,
        ) = await self._search_and_evaluate(
            request,
            rule_engine,
            episode_searches,
        )
        all_evaluated_releases.extend(episode_stage_evaluations)
        all_search_errors.extend(episode_errors)

        best_episodes_by_key: dict[tuple[int, int], ReleaseEvaluation] = {}
        for season, episode, evaluation in episode_candidates:
            assert season is not None
            assert episode is not None
            if not self._is_exact_episode_match(evaluation, season, episode):
                continue
            episode_evaluations.append((season, episode, evaluation))
            key = (season, episode)
            existing = best_episodes_by_key.get(key)
            if existing is None or evaluation.total_score > existing.total_score:
                best_episodes_by_key[key] = evaluation

        for (_season, _episode), evaluation in best_episodes_by_key.items():
            all_selected_releases.append(evaluation)

        logger.info(
            "TV search completed: request_id=%s total_results=%s passing_packs=%s passing_episodes=%s errors=%s",
            request.id,
            len(all_evaluated_releases),
            passing_pack_count,
            len(episode_evaluations),
            len(all_search_errors),
        )

        from app.siftarr.models.activity_log import EventType

        activity_log = ActivityLogService(self.db)
        await activity_log.log(
            EventType.RULE_EVALUATION,
            request_id=request_id,
            details={
                "evaluated": len(all_evaluated_releases),
                "passed_packs": passing_pack_count,
                "passed_episodes": len(episode_evaluations),
                "search_errors": len(all_search_errors),
            },
        )

        stored_releases_by_key = await store_search_results(
            self.db,
            request.id,
            all_evaluated_releases,
        )

        if all_selected_releases:
            all_selected_releases.sort(key=lambda x: x.total_score, reverse=True)

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

            action_result = await use_releases(
                self.db,
                request,
                stored_releases,
                selection_source="rule",
            )

            from app.siftarr.models.activity_log import EventType

            activity_log = ActivityLogService(self.db)
            await activity_log.log(
                EventType.RELEASE_STAGED,
                request_id=request_id,
                details={
                    "release_count": len(all_selected_releases),
                    "titles": [e.release.title for e in all_selected_releases[:5]],
                    "action": action_result.get("status"),
                },
            )

            if action_result.get("status") in ("completed", "downloading", "staged"):
                status_map = {
                    "completed": RequestStatus.COMPLETED,
                    "downloading": RequestStatus.DOWNLOADING,
                    "staged": RequestStatus.STAGED,
                }
                action_status: str = str(action_result.get("status", ""))
                new_status = status_map[action_status]
                for _, covered_seasons in selected_pack_releases:
                    for season in covered_seasons:
                        await self._update_episode_status(request.id, season, None, new_status)
                        await self._update_season_status(request.id, season, new_status)
                for season, episode in best_episodes_by_key:
                    await self._update_episode_status(request.id, season, episode, new_status)
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

        request.status = RequestStatus.PENDING
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

        queue_service = PendingQueueService(self.db)
        await queue_service.add_to_queue(
            request.id,
            error_message=error_msg,
        )

        return {
            "status": "pending",
            "message": f"No releases passed rules. {len(all_evaluated_releases)} releases evaluated.",
            "rejection_reasons": list(set(rejection_reasons))[:5],
            "search_errors": all_errors,
        }
