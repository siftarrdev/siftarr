"""Compatibility wrapper around split release storage and staging actions."""

from app.siftarr.config import get_settings
from app.siftarr.models.request import MediaType, RequestStatus
from app.siftarr.services.pending_queue_service import PendingQueueService
from app.siftarr.services.qbittorrent_service import QbittorrentService
from app.siftarr.services.release_storage import (
    build_prowlarr_release,
    clear_release_search_cache,
    persist_manual_release,
    store_search_results,
)
from app.siftarr.services.staging_actions import use_releases as _use_releases
from app.siftarr.services.staging_service import StagingService

__all__ = [
    "MediaType",
    "RequestStatus",
    "build_prowlarr_release",
    "clear_release_search_cache",
    "get_settings",
    "PendingQueueService",
    "persist_manual_release",
    "QbittorrentService",
    "StagingService",
    "store_search_results",
    "use_releases",
]


async def use_releases(*args, **kwargs):
    """Delegate to staging_actions.use_releases while preserving test seams."""
    from app.siftarr.services import staging_actions

    staging_actions.get_settings = get_settings
    staging_actions.PendingQueueService = PendingQueueService
    staging_actions.QbittorrentService = QbittorrentService
    staging_actions.StagingService = StagingService
    return await _use_releases(*args, **kwargs)
