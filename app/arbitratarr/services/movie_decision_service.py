from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.arbitratarr.models.pending_queue import PendingQueue
from app.arbitratarr.models.request import MediaType, Request, RequestStatus
from app.arbitratarr.models.rule import Rule
from app.arbitratarr.services.prowlarr_service import ProwlarrService
from app.arbitratarr.services.qbittorrent_service import QbittorrentService
from app.arbitratarr.services.rule_engine import RuleEngine


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

        return RuleEngine.from_db_rules(rules=rules)

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
        )

        if not search_result.releases:
            # No results - add to pending queue
            request.status = RequestStatus.PENDING
            await self.db.commit()

            pending_item = PendingQueue(
                request_id=request.id,
                next_retry_at=datetime.now(UTC) + timedelta(hours=24),
                retry_count=0,
            )
            self.db.add(pending_item)
            await self.db.commit()

            return {
                "status": "pending",
                "message": "No releases found in Prowlarr, added to pending queue",
            }

        # Evaluate all releases
        best = rule_engine.get_best_release(search_result.releases)

        if best:
            # Found a passing release
            request.status = RequestStatus.COMPLETED
            await self.db.commit()

            return {
                "status": "completed",
                "selected_release": {
                    "title": best.release.title,
                    "score": best.total_score,
                    "size": best.release.size,
                    "indexer": best.release.indexer,
                    "download_url": best.release.download_url,
                    "magnet_url": best.release.magnet_url,
                },
                "message": f"Selected release with score {best.total_score}",
            }

        # No releases passed rules - add to pending queue
        request.status = RequestStatus.PENDING
        await self.db.commit()

        # Get rejection info
        evaluated = rule_engine.evaluate_batch(search_result.releases)
        rejection_reasons = []
        for e in evaluated:
            if e.rejection_reason:
                rejection_reasons.append(e.rejection_reason)

        pending_item = PendingQueue(
            request_id=request.id,
            next_retry_at=datetime.now(UTC) + timedelta(hours=24),
            retry_count=0,
            last_error="; ".join(set(rejection_reasons))[:500]
            if rejection_reasons
            else "All releases rejected by rules",
        )
        self.db.add(pending_item)
        await self.db.commit()

        return {
            "status": "pending",
            "message": f"No releases passed rules. {len(search_result.releases)} releases evaluated.",
            "rejection_reasons": list(set(rejection_reasons))[:5],
        }
