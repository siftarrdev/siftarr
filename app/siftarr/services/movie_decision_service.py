from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.siftarr.models.release import Release
from app.siftarr.models.request import MediaType, Request, RequestStatus
from app.siftarr.models.rule import Rule
from app.siftarr.services.pending_queue_service import PendingQueueService
from app.siftarr.services.prowlarr_service import ProwlarrService
from app.siftarr.services.qbittorrent_service import QbittorrentService
from app.siftarr.services.release_selection_service import store_search_results, use_releases
from app.siftarr.services.rule_engine import RuleEngine


class MovieDecisionService:
    """
    Service for making download decisions for movie requests.

    Workflow:
    1. Search via Prowlarr with TMDB ID
    2. Run all releases through RuleEngine
    3. Pick highest scoring release that passes all filters
    4. Send to qBittorrent (or staging)
    5. If none pass → add to pending queue
    """

    def __init__(
        self,
        db: AsyncSession,
        prowlarr: ProwlarrService,
        qbittorrent: QbittorrentService,
    ) -> None:
        self.db = db
        self.prowlarr = prowlarr
        self.qbittorrent = qbittorrent

    async def _get_rule_engine(self) -> RuleEngine:
        """Get configured rule engine from database rules."""
        result = await self.db.execute(select(Rule))
        rules = list(result.scalars().all())

        return RuleEngine.from_db_rules(rules=rules, media_type=MediaType.MOVIE.value)

    async def process_request(self, request_id: int) -> dict:
        """
        Process a movie request through the decision workflow.

        Returns:
            Dict with status, selected release, and any errors
        """
        result = await self.db.execute(select(Request).where(Request.id == request_id))
        request = result.scalar_one_or_none()

        if not request:
            return {"status": "error", "message": "Request not found"}

        if request.media_type != MediaType.MOVIE:
            return {"status": "error", "message": "Request is not movie type"}

        # Update status to searching
        request.status = RequestStatus.SEARCHING
        await self.db.commit()

        # Get rule engine
        rule_engine = await self._get_rule_engine()

        # Check we have a valid TMDB ID
        if request.tmdb_id is None:
            request.status = RequestStatus.FAILED
            await self.db.commit()
            return {"status": "error", "message": "No TMDB ID available for movie"}

        # Search for movie
        search_result = await self.prowlarr.search_by_tmdbid(
            tmdbid=request.tmdb_id,
            title=request.title,
            year=request.year,
        )

        if not search_result.releases:
            # No results - add to pending queue
            request.status = RequestStatus.PENDING
            await self.db.commit()

            queue_service = PendingQueueService(self.db)
            await queue_service.add_to_queue(request.id)

            return {
                "status": "pending",
                "message": "No releases found in Prowlarr, added to pending queue",
            }

        all_evaluated = [rule_engine.evaluate(release) for release in search_result.releases]
        await store_search_results(self.db, request.id, all_evaluated)

        passed_results = [evaluation for evaluation in all_evaluated if evaluation.passed]
        best = passed_results[0] if passed_results else None

        if best:
            release_result = await self.db.execute(
                select(Release).where(
                    Release.request_id == request.id,
                    Release.title == best.release.title,
                )
            )
            stored_release = release_result.scalar_one_or_none()
            action_result = await use_releases(
                self.db,
                request,
                [stored_release] if stored_release else [],
                selection_source="rule",
            )

            return {
                "status": action_result["status"],
                "selected_release": {
                    "title": best.release.title,
                    "score": best.total_score,
                    "size": best.release.size,
                    "indexer": best.release.indexer,
                    "download_url": best.release.download_url,
                    "magnet_url": best.release.magnet_url,
                },
                "message": action_result["message"],
            }

        # No releases passed rules - add to pending queue
        request.status = RequestStatus.PENDING
        await self.db.commit()

        rejection_reasons = []
        for e in all_evaluated:
            if e.rejection_reason:
                rejection_reasons.append(e.rejection_reason)

        queue_service = PendingQueueService(self.db)
        await queue_service.add_to_queue(
            request.id,
            error_message="; ".join(set(rejection_reasons))[:500]
            if rejection_reasons
            else "All releases rejected by rules",
        )

        return {
            "status": "pending",
            "message": f"No releases passed rules. {len(search_result.releases)} releases evaluated.",
            "rejection_reasons": list(set(rejection_reasons))[:5],
        }
