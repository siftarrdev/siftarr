"""Service for interacting with Overseerr API."""

import httpx

from app.arbitratarr.config import get_settings


class OverseerrService:
    """Service for fetching media details from Overseerr."""

    def __init__(self) -> None:
        """Initialize the Overseerr service."""
        self.settings = get_settings()
        self.base_url = str(self.settings.overseerr_url)
        self.api_key = self.settings.overseerr_api_key

    async def get_media_details(self, media_type: str, external_id: int) -> dict | None:
        """Fetch media details from Overseerr.

        Args:
            media_type: The type of media ('movie' or 'tv').
            external_id: The TMDB ID for movies or TVDB ID for TV shows.

        Returns:
            A dict containing media details if successful, None otherwise.
        """
        if not self.base_url or not self.api_key:
            return None

        endpoint = f"{self.base_url}/api/v1/{media_type}/{external_id}"
        headers = {"X-Api-Key": self.api_key}

        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(endpoint, headers=headers, timeout=30.0)
                if response.status_code == 200:
                    return response.json()
                return None
            except httpx.RequestError:
                return None

    async def get_request(self, request_id: int) -> dict | None:
        """Get full request details from Overseerr.

        Args:
            request_id: The Overseerr request ID.

        Returns:
            A dict containing request details if successful, None otherwise.
        """
        if not self.base_url or not self.api_key:
            return None

        endpoint = f"{self.base_url}/api/v1/request/{request_id}"
        headers = {"X-Api-Key": self.api_key}

        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(endpoint, headers=headers, timeout=30.0)
                if response.status_code == 200:
                    return response.json()
                return None
            except httpx.RequestError:
                return None
