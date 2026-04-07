import asyncio
import json

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.arbitratarr.models.release import Release
from app.arbitratarr.models.request import MediaType, Request, RequestStatus
from app.arbitratarr.models.rule import Rule
from app.arbitratarr.services.pending_queue_service import PendingQueueService
from app.arbitratarr.services.prowlarr_service import ProwlarrService
from app.arbitratarr.services.qbittorrent_service import QbittorrentService
from app.arbitratarr.services.release_selection_service import store_search_results, use_releases
from app.arbitratarr.services.rule_engine import ReleaseEvaluation, RuleEngine


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

    async def _get_rule_engine(self) -> RuleEngine:
        """Get configured rule engine from database rules."""
        result = await self.db.execute(select(Rule))
        rules = list(result.scalars().all())

        return RuleEngine.from_db_rules(rules=rules, media_type=MediaType.TV.value)

    def _get_requested_seasons(self, request: Request) -> list[int]:
        """Parse requested seasons from JSON string."""
        if not request.requested_seasons:
            return []
        try:
            return json.loads(request.requested_seasons)
        except (json.JSONDecodeError, TypeError):
            return []

    def _get_requested_episodes(self, request: Request) -> dict[int, list[int]]:
        """
        Parse requested episodes by season.
        Returns: {season: [episode, episode, ...]}
        """
        if not request.requested_episodes:
            return {}
        try:
            data = json.loads(request.requested_episodes)
            if isinstance(data, dict):
                return {int(k): v for k, v in data.items()}
            return {}
        except (json.JSONDecodeError, TypeError, ValueError):
            return {}

    async def process_request(self, request_id: int) -> dict:
        """
        Process a TV request through the season-first workflow.

        Returns:
            Dict with status, selected releases, and any errors
        """
        result = await self.db.execute(select(Request).where(Request.id == request_id))
        request = result.scalar_one_or_none()

        if not request:
            return {"status": "error", "message": "Request not found"}

        if request.media_type != MediaType.TV:
            return {"status": "error", "message": "Request is not TV type"}

        # Update status to searching
        request.status = RequestStatus.SEARCHING
        await self.db.commit()

        # Get rule engine
        rule_engine = await self._get_rule_engine()

        # Check we have a valid TVDB ID
        if request.tvdb_id is None:
            request.status = RequestStatus.FAILED
            await self.db.commit()
            return {"status": "error", "message": "No TVDB ID available for TV show"}

        # Get requested seasons
        requested_seasons = self._get_requested_seasons(request)
        requested_episodes = self._get_requested_episodes(request)

        if not requested_seasons:
            return {"status": "error", "message": "No seasons specified"}

        all_evaluated_releases: list[ReleaseEvaluation] = []
        all_selected_releases: list[ReleaseEvaluation] = []
        season_pack_selected = False

        # Step 1: Try season packs - search all seasons concurrently
        season_search_coros = [
            self.prowlarr.search_by_tvdbid(
                tvdbid=request.tvdb_id,
                title=request.title,
                season=season,
                year=request.year,
            )
            for season in requested_seasons
        ]
        season_results = await asyncio.gather(*season_search_coros)

        for search_result in season_results:
            if not search_result.releases:
                continue

            evaluated_packs = [rule_engine.evaluate(release) for release in search_result.releases]
            all_evaluated_releases.extend(evaluated_packs)
            passing_packs = [evaluation for evaluation in evaluated_packs if evaluation.passed]
            best_pack = passing_packs[0] if passing_packs else None

            if best_pack and best_pack.passed:
                all_selected_releases.append(best_pack)
                season_pack_selected = True
                break  # Found a good season pack

        # Step 2: If no season pack, try individual episodes
        if not season_pack_selected:
            episode_search_coros = []
            for season in requested_seasons:
                episodes_to_search = requested_episodes.get(season, [])

                if not episodes_to_search:
                    episode_search_coros.append(
                        self.prowlarr.search_by_tvdbid(
                            tvdbid=request.tvdb_id,
                            title=request.title,
                            season=season,
                            year=request.year,
                        )
                    )
                else:
                    for episode in episodes_to_search:
                        episode_search_coros.append(
                            self.prowlarr.search_by_tvdbid(
                                tvdbid=request.tvdb_id,
                                title=request.title,
                                season=season,
                                episode=episode,
                                year=request.year,
                            )
                        )

            episode_results = await asyncio.gather(*episode_search_coros)

            for search_result in episode_results:
                if not search_result.releases:
                    continue

                evaluated = [rule_engine.evaluate(release) for release in search_result.releases]
                all_evaluated_releases.extend(evaluated)
                passing = [result_item for result_item in evaluated if result_item.passed]
                best = passing[0] if passing else None
                if best:
                    all_selected_releases.append(best)

        await store_search_results(self.db, request.id, all_evaluated_releases)

        # Step 3: If we have selected releases, send to qBittorrent
        if all_selected_releases:
            # Sort by score
            all_selected_releases.sort(key=lambda x: x.total_score, reverse=True)

            selected_titles = {result_item.release.title for result_item in all_selected_releases}
            stored_releases_result = await self.db.execute(
                select(Release).where(
                    Release.request_id == request.id, Release.title.in_(selected_titles)
                )
            )
            stored_releases = list(stored_releases_result.scalars().all())
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

        queue_service = PendingQueueService(self.db)
        await queue_service.add_to_queue(request.id)

        return {
            "status": "pending",
            "message": "No releases passed rules, added to pending queue",
        }
