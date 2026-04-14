import asyncio
import json
import logging
from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.siftarr.config import get_settings
from app.siftarr.models.episode import Episode
from app.siftarr.models.release import Release
from app.siftarr.models.request import MediaType, Request, RequestStatus
from app.siftarr.models.rule import Rule
from app.siftarr.models.season import Season
from app.siftarr.services.pending_queue_service import PendingQueueService
from app.siftarr.services.prowlarr_service import ProwlarrSearchResult, ProwlarrService
from app.siftarr.services.qbittorrent_service import QbittorrentService
from app.siftarr.services.release_parser import parse_release_coverage
from app.siftarr.services.release_selection_service import store_search_results, use_releases
from app.siftarr.services.rule_engine import ReleaseEvaluation, RuleEngine

MAX_CONCURRENT_SEARCHES = 5

logger = logging.getLogger(__name__)


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
        self._search_semaphore = asyncio.Semaphore(MAX_CONCURRENT_SEARCHES)
        self._settings = get_settings()

    async def _get_rule_engine(self) -> RuleEngine:
        result = await self.db.execute(select(Rule))
        rules = list(result.scalars().all())
        return RuleEngine.from_db_rules(rules=rules, media_type=MediaType.TV.value)

    def _get_requested_seasons(self, request: Request) -> list[int]:
        if not request.requested_seasons:
            return []
        try:
            return json.loads(request.requested_seasons)
        except (json.JSONDecodeError, TypeError):
            return []

    def _get_requested_episodes(self, request: Request) -> dict[int, list[int]]:
        if not request.requested_episodes:
            return {}
        try:
            data = json.loads(request.requested_episodes)
            if isinstance(data, dict):
                return {int(k): [int(episode) for episode in v] for k, v in data.items()}
            if isinstance(data, list):
                episodes = [int(episode) for episode in data if isinstance(episode, int | str)]
                if not episodes:
                    return {}
                seasons = self._get_requested_seasons(request)
                return dict.fromkeys(seasons, episodes)
            return {}
        except (json.JSONDecodeError, TypeError, ValueError):
            return {}

    async def _limited_search(self, coro):
        async with self._search_semaphore:
            return await coro

    @staticmethod
    def _release_key(evaluation: ReleaseEvaluation) -> str:
        return evaluation.release.info_hash or evaluation.release.title

    @staticmethod
    def _get_release_season_coverage(
        evaluation: ReleaseEvaluation, requested_seasons: Iterable[int]
    ) -> set[int]:
        requested_season_set = set(requested_seasons)
        coverage = parse_release_coverage(evaluation.release.title)
        if coverage.episode_number is not None:
            return set()
        if coverage.is_complete_series:
            return requested_season_set
        return set(coverage.season_numbers).intersection(requested_season_set)

    def _select_pack_releases(
        self, pack_evaluations: list[ReleaseEvaluation], requested_seasons: list[int]
    ) -> tuple[list[tuple[ReleaseEvaluation, set[int]]], set[int]]:
        deduped_candidates: dict[str, tuple[ReleaseEvaluation, set[int]]] = {}

        for evaluation in pack_evaluations:
            season_coverage = self._get_release_season_coverage(evaluation, requested_seasons)
            if not season_coverage:
                continue

            key = self._release_key(evaluation)
            existing = deduped_candidates.get(key)
            if existing is None or (
                len(season_coverage),
                evaluation.total_score,
            ) > (
                len(existing[1]),
                existing[0].total_score,
            ):
                deduped_candidates[key] = (evaluation, season_coverage)

        selected_releases: list[tuple[ReleaseEvaluation, set[int]]] = []
        uncovered_seasons = set(requested_seasons)

        for evaluation, season_coverage in sorted(
            deduped_candidates.values(),
            key=lambda item: (len(item[1]), item[0].total_score),
            reverse=True,
        ):
            if season_coverage.isdisjoint(uncovered_seasons):
                continue
            selected_releases.append((evaluation, set(season_coverage)))
            uncovered_seasons.difference_update(season_coverage)

        return selected_releases, uncovered_seasons

    async def _get_db_episodes_for_season(self, request_id: int, season_number: int) -> list[int]:
        result = await self.db.execute(
            select(Episode.episode_number)
            .join(Season, Episode.season_id == Season.id)
            .where(Season.request_id == request_id, Season.season_number == season_number)
            .order_by(Episode.episode_number)
        )
        return [row[0] for row in result.all()]

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
        result = await self.db.execute(select(Request).where(Request.id == request_id))
        request = result.scalar_one_or_none()

        if not request:
            return {"status": "error", "message": "Request not found"}

        if request.media_type != MediaType.TV:
            return {"status": "error", "message": "Request is not TV type"}

        request.status = RequestStatus.SEARCHING
        await self.db.commit()

        logger.info(
            "TV search started: request_id=%s title=%s tvdb_id=%s seasons=%s episodes=%s",
            request.id,
            request.title,
            request.tvdb_id,
            request.requested_seasons,
            request.requested_episodes,
        )

        rule_engine = await self._get_rule_engine()

        if request.tvdb_id is None:
            request.status = RequestStatus.FAILED
            await self.db.commit()
            return {"status": "error", "message": "No TVDB ID available for TV show"}

        requested_seasons = self._get_requested_seasons(request)
        requested_episodes = self._get_requested_episodes(request)

        logger.info(
            "TV search parsed request: request_id=%s seasons=%s episodes_by_season=%s",
            request.id,
            requested_seasons,
            requested_episodes,
        )

        if not requested_seasons:
            return {"status": "error", "message": "No seasons specified"}

        search_tasks: list[tuple[str, int | None, int | None]] = []

        if len(requested_seasons) > 1:
            search_tasks.append(("broad_pack", None, None))

        for season in requested_seasons:
            search_tasks.append(("season_pack", season, None))
            episodes_in_season = requested_episodes.get(season, [])
            if not episodes_in_season:
                db_episodes = await self._get_db_episodes_for_season(request.id, season)
                if db_episodes:
                    episodes_in_season = db_episodes[: self._settings.max_episode_discovery]
                else:
                    episodes_in_season = list(range(1, self._settings.max_episode_discovery + 1))
            for ep in episodes_in_season:
                search_tasks.append(("episode", season, ep))

        search_coros = []
        for task_type, season, episode in search_tasks:
            if task_type in {"season_pack", "broad_pack"}:
                search_coros.append(
                    self._limited_search(
                        self.prowlarr.search_by_tvdbid(
                            tvdbid=request.tvdb_id,
                            title=request.title,
                            season=season,
                            year=request.year,
                        )
                    )
                )
            else:
                search_coros.append(
                    self._limited_search(
                        self.prowlarr.search_by_tvdbid(
                            tvdbid=request.tvdb_id,
                            title=request.title,
                            season=season,
                            episode=episode,
                            year=request.year,
                        )
                    )
                )

        all_results = await asyncio.gather(*search_coros, return_exceptions=True)

        all_evaluated_releases: list[ReleaseEvaluation] = []
        all_search_errors: list[str] = []
        pack_evaluations: list[ReleaseEvaluation] = []
        episode_evaluations: list[tuple[int, int, ReleaseEvaluation]] = []

        for i, sr in enumerate(all_results):
            task_type, season, episode = search_tasks[i]
            if isinstance(sr, Exception):
                logger.warning(
                    "TV search failed: request_id=%s type=%s season=%s episode=%s error=%s",
                    request.id,
                    task_type,
                    season,
                    episode,
                    sr,
                )
                all_search_errors.append(str(sr))
                continue
            if not isinstance(sr, ProwlarrSearchResult):
                all_search_errors.append("Unexpected search result type")
                continue
            if sr.error:
                logger.warning(
                    "TV search error: request_id=%s type=%s season=%s episode=%s error=%s",
                    request.id,
                    task_type,
                    season,
                    episode,
                    sr.error,
                )
                all_search_errors.append(sr.error)
                continue

            for release in sr.releases:
                evaluation = rule_engine.evaluate(release)
                all_evaluated_releases.append(evaluation)
                if evaluation.passed:
                    coverage = parse_release_coverage(release.title)
                    if coverage.episode_number is None and (
                        coverage.season_numbers or coverage.is_complete_series
                    ):
                        pack_evaluations.append(evaluation)
                    elif task_type == "episode":
                        assert episode is not None
                        episode_evaluations.append((season, episode, evaluation))

        logger.info(
            "TV search completed: request_id=%s total_results=%s passing_packs=%s passing_episodes=%s errors=%s",
            request.id,
            len(all_evaluated_releases),
            len(pack_evaluations),
            len(episode_evaluations),
            len(all_search_errors),
        )

        all_selected_releases: list[ReleaseEvaluation] = []

        selected_pack_releases, uncovered_seasons = self._select_pack_releases(
            pack_evaluations, requested_seasons
        )
        seasons_with_packs: set[int] = set()

        for pack_eval, covered_seasons in selected_pack_releases:
            all_selected_releases.append(pack_eval)
            seasons_with_packs.update(covered_seasons)
            for season in covered_seasons:
                await self._update_episode_status(request.id, season, None, RequestStatus.SEARCHING)
                await self._update_season_status(request.id, season, RequestStatus.SEARCHING)

        episodes_by_key: dict[tuple[int, int], ReleaseEvaluation] = {}
        for season, episode, ep_eval in episode_evaluations:
            key = (season, episode)
            if key not in episodes_by_key or ep_eval.total_score > episodes_by_key[key].total_score:
                episodes_by_key[key] = ep_eval

        for (season, episode), ep_eval in episodes_by_key.items():
            if season in uncovered_seasons:
                all_selected_releases.append(ep_eval)
                await self._update_episode_status(
                    request.id, season, episode, RequestStatus.SEARCHING
                )

        await store_search_results(self.db, request.id, all_evaluated_releases)

        if all_selected_releases:
            all_selected_releases.sort(key=lambda x: x.total_score, reverse=True)

            selected_titles = {e.release.title for e in all_selected_releases}
            stored_releases_result = await self.db.execute(
                select(Release).where(
                    Release.request_id == request.id, Release.title.in_(selected_titles)
                )
            )
            stored_releases = list(stored_releases_result.scalars().all())

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
                for season, episode in episodes_by_key:
                    if season in uncovered_seasons:
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
