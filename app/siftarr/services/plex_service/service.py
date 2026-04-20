import logging

import httpx

from app.siftarr.config import Settings, get_settings
from app.siftarr.services.http_client import get_shared_client

from .cache import PlexServiceCacheMixin
from .episodes import PlexServiceEpisodesMixin
from .library_scan import PlexServiceLibraryScanMixin
from .lookup import PlexServiceLookupMixin

logger = logging.getLogger(__name__)


class PlexService(
    PlexServiceLookupMixin,
    PlexServiceEpisodesMixin,
    PlexServiceLibraryScanMixin,
    PlexServiceCacheMixin,
):
    """Service for fetching per-episode availability from Plex."""

    def __init__(self, settings: Settings | None = None) -> None:
        """Initialize the Plex service."""
        self.settings = settings or get_settings()
        self.base_url = str(self.settings.plex_url).rstrip("/") if self.settings.plex_url else None
        self.token = self.settings.plex_token
        self._scan_cycle_depth = 0
        self._scan_cycle_guid_cache = {}
        self._scan_cycle_rating_key_cache = {}
        self._scan_cycle_sections_cache = {}

    def _get_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {"Accept": "application/json"}
        if self.token:
            headers["X-Plex-Token"] = self.token
        return headers

    async def _get_client(self) -> httpx.AsyncClient:
        return await get_shared_client()

    async def close(self) -> None:
        """Close the service (no-op since using shared client)."""
        pass
