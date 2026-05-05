"""Search service for request processing and manual release selection."""

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.siftarr.config import get_settings
from app.siftarr.models import EventType
from app.siftarr.models.request import MediaType
from app.siftarr.models.request import Request as RequestModel
from app.siftarr.models.rule import Rule
from app.siftarr.services.activity_log_service import ActivityLogService
from app.siftarr.services.lifecycle_service import LifecycleService
from app.siftarr.services.media_helpers import extract_media_title_and_year
from app.siftarr.services.movie_decision_service import MovieDecisionService
from app.siftarr.services.overseerr_service import OverseerrService
from app.siftarr.services.pending_queue_service import PendingQueueService
from app.siftarr.services.prowlarr_service import ProwlarrRelease, ProwlarrService
from app.siftarr.services.qbittorrent_service import QbittorrentService
from app.siftarr.services.release_storage import persist_manual_release
from app.siftarr.services.rule_engine import ReleaseEvaluation, RuleEngine
from app.siftarr.services.staging_actions import use_releases
from app.siftarr.services.tv_decision_service import TVDecisionService

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
        rules_result = await self.db.execute(select(Rule))
        rules = list(rules_result.scalars().all())
        engine = RuleEngine.from_db_rules(rules=rules, media_type=request.media_type.value)
        return engine.evaluate(release)

    async def select_manual_release(
        self,
        request: RequestModel,
        release: ProwlarrRelease,
    ) -> dict[str, object]:
        """Persist and use a manual-search release through the normal selection path."""
        evaluation = await self.evaluate_manual_release(request, release)
        stored_release = await persist_manual_release(self.db, request, release, evaluation)
        return await use_releases(self.db, request, [stored_release], selection_source="manual")

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
            finally:
                await overseerr.close()

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
