"""Overseerr metadata lookup for dashboard detail responses."""

from __future__ import annotations

import asyncio
from typing import Any

from app.siftarr.config import Settings
from app.siftarr.services.dashboard.dashboard_service import DashboardOverseerrDetails
from app.siftarr.services.integrations.overseerr_service import (
    OverseerrService,
    build_overseerr_media_url,
    build_poster_url,
)

_RELEASE_DATE_KEYS = {"movie": "releaseDate", "tv": "firstAirDate"}


class MetadataService:
    """Load Overseerr metadata for request details."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def load_overseerr_details(self, request: Any) -> DashboardOverseerrDetails | None:
        """Fetch and format Overseerr media details for a request."""
        if not request.overseerr_request_id:
            return None

        overseerr_service = OverseerrService(settings=self.settings)
        ov_task = asyncio.create_task(overseerr_service.get_request(request.overseerr_request_id))
        media_details_task = None
        media_type = request.media_type.value
        if media_type in _RELEASE_DATE_KEYS and request.tmdb_id:
            media_details_task = asyncio.create_task(
                overseerr_service.get_media_details(media_type, request.tmdb_id)
            )

        ov = await ov_task
        media: dict[str, object] = {}
        request_status = "unknown"
        if ov:
            media = ov.get("media") or {}
            request_status = overseerr_service.normalize_media_status(media.get("status"))

        media_details = await media_details_task if media_details_task else None
        merged_media = {**media, **(media_details or {})}
        overview_value = merged_media.get("overview") or merged_media.get("summary")

        release_date: str | None = None
        if media_details and media_type in _RELEASE_DATE_KEYS:
            release_date = media_details.get(_RELEASE_DATE_KEYS[media_type])

        return DashboardOverseerrDetails(
            overview=str(overview_value) if overview_value else "",
            poster=build_poster_url(merged_media.get("posterPath") or merged_media.get("poster")),
            status=request_status,
            url=build_overseerr_media_url(
                self.settings.overseerr_url,
                media_type,
                request.tmdb_id,
            ),
            release_date=release_date,
        )
