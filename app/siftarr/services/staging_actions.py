"""Backward-compat re-exports from ``staging_service``.

New code should import directly from ``staging_service``:
    from app.siftarr.services.releases.staging_service import StagingService, download_torrent, ...
"""

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.siftarr.models.release import Release
from app.siftarr.models.request import Request
from app.siftarr.services.releases.staging_service import (
    StagingService,
    download_torrent,
    validate_torrent_file,
)

__all__ = [
    "StagingService",
    "download_torrent",
    "validate_torrent_file",
    "use_releases",
]

logger = logging.getLogger(__name__)


async def use_releases(
    db: AsyncSession,
    request: Request,
    releases: list[Release],
    *,
    selection_source: str = "manual",
) -> dict[str, object]:
    """Backward-compat wrapper around ``StagingService(db).use_releases()``."""
    return await StagingService(db).use_releases(
        request,
        releases,
        selection_source=selection_source,
    )
