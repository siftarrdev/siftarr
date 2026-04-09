import asyncio
import json
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.siftarr.models.release import Release
from app.siftarr.models.request import MediaType, Request, RequestStatus
from app.siftarr.models.rule import Rule
from app.siftarr.services.pending_queue_service import PendingQueueService
from app.siftarr.services.prowlarr_service import ProwlarrSearchResult, ProwlarrService
from app.siftarr.services.qbittorrent_service import QbittorrentService
from app.siftarr.services.release_selection_service import store_search_results, use_releases
from app.siftarr.services.rule_engine import ReleaseEvaluation, RuleEngine

MAX_CONCURRENT_SEARCHES = 5

logger = logging.getLogger(__name__)


class TVDecisionService:
    """
    Service for making download decisions for TV requests.

    Workflow (Season-First):
    1. Search for season pack via Prowlarr
    2. Evaluate all packs through RuleEngine
    3. If a pack passes all filters and has highest score → send to qBit (or staging)
    4. If no season pack passes → search individual episodes
    5. Evaluate episodes one by one
    6. Send passing episodes to qBit (or staging)
    7. If nothing passes → add to pending queue
    """

    def __init__(
        self,
        db: AsyncSession,
        prowlarr: ProwlarrService,
        qbittorrent: QbittorrentService,
    ):
        self.db = db
        self.prowlarr = prowlarr
        self.qbittorrent = qbittorrent
        self._search_semaphore = asyncio.Semaphore(MAX_CONCURRENT_SEARCHES)

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

        all_evaluated_releases: list[ReleaseEvaluation] = []
        all_selected_releases: list[ReleaseEvaluation] = []
        season_pack_selected = False

        # Step 1: Try season packs - search all seasons concurrently
        season_search_coros = [
            self._limited_search(
                self.prowlarr.search_by_tvdbid(
                    tvdbid=request.tvdb_id,
                    title=request.title,
                    season=season,
                    year=request.year,
                )
            )
            for season in requested_seasons
        ]
        season_results = await asyncio.gather(*season_search_coros, return_exceptions=True)

        season_search_successes = []
        for i, sr in enumerate(season_results):
            if isinstance(sr, Exception):
                logger.warning(
                    "TV season search failed: request_id=%s season=%s error=%s",
                    request.id,
                    requested_seasons[i],
                    sr,
                )
            elif isinstance(sr, ProwlarrSearchResult):
                season_search_successes.append(sr)

        logger.info(
            "TV season search completed: request_id=%s queries=%s results=%s",
            request.id,
            len(season_search_coros),
            [len(sr.releases) for sr in season_search_successes],
        )

        all_passing_packs: list[ReleaseEvaluation] = []
        for search_result in season_search_successes:
            if not search_result.releases:
                continue

            evaluated_packs = [rule_engine.evaluate(release) for release in search_result.releases]
            all_evaluated_releases.extend(evaluated_packs)
            passing_packs = [e for e in evaluated_packs if e.passed]
            all_passing_packs.extend(passing_packs)

        if all_passing_packs:
            all_passing_packs.sort(key=lambda e: e.total_score, reverse=True)
            best_pack = all_passing_packs[0]
            all_selected_releases.append(best_pack)
            season_pack_selected = True

        # Step 2: If no season pack, try individual episodes
        if not season_pack_selected:
            episode_search_coros = []
            for season in requested_seasons:
                episodes_to_search = requested_episodes.get(season, [])

                if not episodes_to_search:
                    episode_search_coros.append(
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
                    for episode in episodes_to_search:
                        episode_search_coros.append(
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

            episode_results = await asyncio.gather(*episode_search_coros, return_exceptions=True)

            episode_search_successes = []
            for er in episode_results:
                if isinstance(er, Exception):
                    logger.warning(
                        "TV episode search failed: request_id=%s error=%s",
                        request.id,
                        er,
                    )
                elif isinstance(er, ProwlarrSearchResult):
                    episode_search_successes.append(er)

            logger.info(
                "TV episode search completed: request_id=%s queries=%s results=%s",
                request.id,
                len(episode_search_coros),
                [len(er.releases) for er in episode_search_successes],
            )

            for search_result in episode_search_successes:
                if not search_result.releases:
                    continue

                evaluated = [rule_engine.evaluate(release) for release in search_result.releases]
                all_evaluated_releases.extend(evaluated)
                passing = [e for e in evaluated if e.passed]
                best = passing[0] if passing else None
                if best:
                    all_selected_releases.append(best)

        await store_search_results(self.db, request.id, all_evaluated_releases)

        # Step 3: If we have selected releases, send to qBittorrent
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

        # Step 4: No releases passed - add to pending queue
        request.status = RequestStatus.PENDING
        await self.db.commit()

        rejection_reasons = []
        for e in all_evaluated_releases:
            if e.rejection_reason:
                rejection_reasons.append(e.rejection_reason)

        logger.info(
            "TV search rejected all releases: request_id=%s evaluated=%s rejection_reasons=%s",
            request.id,
            len(all_evaluated_releases),
            list(set(rejection_reasons))[:5],
        )

        queue_service = PendingQueueService(self.db)
        await queue_service.add_to_queue(
            request.id,
            error_message="; ".join(set(rejection_reasons))[:500]
            if rejection_reasons
            else "All releases rejected by rules",
        )

        return {
            "status": "pending",
            "message": f"No releases passed rules. {len(all_evaluated_releases)} releases evaluated.",
            "rejection_reasons": list(set(rejection_reasons))[:5],
        }
