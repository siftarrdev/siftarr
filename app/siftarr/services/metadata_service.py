"""Overseerr metadata lookup for dashboard detail responses."""

from __future__ import annotations

import asyncio
from typing import Any, cast

from app.siftarr.config import Settings
from app.siftarr.services.dashboard_service import DashboardOverseerrDetails
from app.siftarr.services.overseerr_service import (
    OverseerrService,
    build_overseerr_media_url,
    build_poster_url,
)


def extract_imdb_id(media: dict[str, object] | None) -> str | None:
    """Return an IMDb ID from common Overseerr media detail shapes."""
    if not media:
        return None
    for key in ("imdbId", "imdb_id", "imdbID"):
        value = media.get(key)
        if value:
            return str(value)
    external_ids = media.get("externalIds")
    if isinstance(external_ids, dict):
        typed_external_ids = cast("dict[str, object]", external_ids)
        for key in ("imdbId", "imdb_id", "imdbID"):
            value = typed_external_ids.get(key)
            if value:
                return str(value)
    return None


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
        if request.media_type.value == "movie" and request.tmdb_id:
            media_details_task = asyncio.create_task(
                overseerr_service.get_media_details("movie", request.tmdb_id)
            )
        elif request.media_type.value == "tv" and request.tmdb_id:
            media_details_task = asyncio.create_task(
                overseerr_service.get_media_details("tv", request.tmdb_id)
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
        if media_details:
            if request.media_type.value == "movie":
                release_date = media_details.get("releaseDate")  # type: ignore[union-attr]
            elif request.media_type.value == "tv":
                release_date = media_details.get("firstAirDate")  # type: ignore[union-attr]

        return DashboardOverseerrDetails(
            overview=str(overview_value) if overview_value else "",
            poster=build_poster_url(merged_media.get("posterPath") or merged_media.get("poster")),
            status=request_status,
            url=build_overseerr_media_url(
                self.settings.overseerr_url,
                request.media_type.value,
                request.tmdb_id,
            ),
            release_date=release_date,
        )
