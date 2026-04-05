import json

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.arbitratarr.models.pending_queue import PendingQueue
from app.arbitratarr.models.request import MediaType, Request, RequestStatus
from app.arbitratarr.models.rule import Rule
from app.arbitratarr.services.prowlarr_service import ProwlarrService
from app.arbitratarr.services.qbittorrent_service import QbittorrentService
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

        return RuleEngine.from_db_rules(rules=rules)

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

        # Get requested seasons
        requested_seasons = self._get_requested_seasons(request)
        requested_episodes = self._get_requested_episodes(request)

        if not requested_seasons:
            return {"status": "error", "message": "No seasons specified"}

        all_selected_releases: list[ReleaseEvaluation] = []
        season_pack_selected = False

        # Step 1: Try season packs
        for season in requested_seasons:
            search_result = await self.prowlarr.search_by_tvdbid(
                tvdbid=request.tvdb_id,
                season=season,
            )

            if not search_result.releases:
                continue

            # Evaluate season packs
            best_pack = rule_engine.get_best_release(search_result.releases)

            if best_pack and best_pack.passed:
                all_selected_releases.append(best_pack)
                season_pack_selected = True
                break  # Found a good season pack, no need for episode searches

        # Step 2: If no season pack, try individual episodes
        if not season_pack_selected:
            for season in requested_seasons:
                episodes_to_search = requested_episodes.get(season, [])

                if not episodes_to_search:
                    # If no specific episodes requested, get all episodes for season
                    # This is a simplification - real implementation would query Overseerr
                    search_result = await self.prowlarr.search_by_tvdbid(
                        tvdbid=request.tvdb_id,
                        season=season,
                    )

                    if not search_result.releases:
                        continue

                    # Evaluate and collect passing releases
                    evaluated = rule_engine.evaluate_batch(search_result.releases)
                    all_selected_releases.extend(evaluated)
                else:
                    # Search for specific episodes
                    for episode in episodes_to_search:
                        search_result = await self.prowlarr.search_by_tvdbid(
                            tvdbid=request.tvdb_id,
                            season=season,
                            episode=episode,
                        )

                        if not search_result.releases:
                            continue

                        best = rule_engine.get_best_release(search_result.releases)
                        if best:
                            all_selected_releases.append(best)

        # Step 3: If we have selected releases, send to qBittorrent
        if all_selected_releases:
            # Sort by score
            all_selected_releases.sort(key=lambda x: x.total_score, reverse=True)

            # Mark request as completed
            request.status = RequestStatus.COMPLETED
            await self.db.commit()

            return {
                "status": "completed",
                "selected_releases": [
                    {
                        "title": e.release.title,
                        "score": e.total_score,
                        "download_url": e.release.download_url,
                        "magnet_url": e.release.magnet_url,
                    }
                    for e in all_selected_releases
                ],
                "message": f"Found {len(all_selected_releases)} suitable release(s)",
            }

        # Step 4: No releases passed - add to pending queue
        request.status = RequestStatus.PENDING
        await self.db.commit()

        # Add to pending queue
        from datetime import datetime, timedelta

        pending_item = PendingQueue(
            request_id=request.id,
            next_retry_at=datetime.utcnow() + timedelta(hours=24),
            retry_count=0,
        )
        self.db.add(pending_item)
        await self.db.commit()

        return {
            "status": "pending",
            "message": "No releases passed rules, added to pending queue",
        }
