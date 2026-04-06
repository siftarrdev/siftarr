"""Service for interacting with Overseerr API."""

from typing import Any

import httpx

from app.arbitratarr.config import get_settings


class OverseerrService:
    """Service for fetching media details from Overseerr."""

    def __init__(self) -> None:
        """Initialize the Overseerr service."""
        self.settings = get_settings()
        # Strip trailing slash to avoid double slashes in API URL
        self.base_url = str(self.settings.overseerr_url).rstrip("/")
        self.api_key = self.settings.overseerr_api_key
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create an async HTTP client."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                headers={"X-Api-Key": self.api_key or ""},
                timeout=30.0,
            )
        return self._client

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def get_requests(
        self,
        status: str = "approved",
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """
        Fetch requests from Overseerr.

        Args:
            status: Filter by request status ('approved', 'pending', etc.)
            limit: Maximum number of results to return

        Returns:
            List of request dictionaries from Overseerr API.
        """
        if not self.base_url or not self.api_key:
            return []

        endpoint = f"{self.base_url}/api/v1/request"
        client = await self._get_client()

        try:
            response = await client.get(
                endpoint,
                params={"take": limit, "filter": status},
            )
            if response.status_code == 200:
                data = response.json()
                # Overseerr returns { "results": [...] } or just [...] depending on version
                if isinstance(data, dict) and "results" in data:
                    return data["results"]
                if isinstance(data, dict) and "data" in data:
                    return data["data"]
                return data if isinstance(data, list) else []
            elif response.status_code == 401:
                # Unauthorized - API key might be invalid
                return []
            return []
        except httpx.RequestError:
            return []

    async def get_request(self, request_id: int) -> dict[str, Any] | None:
        """Get full request details from Overseerr.

        Args:
            request_id: The Overseerr request ID.

        Returns:
            A dict containing request details if successful, None otherwise.
        """
        if not self.base_url or not self.api_key:
            return None

        endpoint = f"{self.base_url}/api/v1/request/{request_id}"
        client = await self._get_client()

        try:
            response = await client.get(endpoint)
            if response.status_code == 200:
                return response.json()
            return None
        except httpx.RequestError:
            return None

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

    async def approve_request(self, request_id: int) -> bool:
        """Approve a request in Overseerr via API."""
        if not self.base_url or not self.api_key:
            return False

        endpoint = f"{self.base_url}/api/v1/request/{request_id}/approve"
        client = await self._get_client()

        try:
            response = await client.post(endpoint)
            return response.status_code == 200
        except httpx.RequestError:
            return False

    async def decline_request(self, request_id: int, reason: str | None = None) -> bool:
        """Decline a request in Overseerr via API."""
        if not self.base_url or not self.api_key:
            return False

        endpoint = f"{self.base_url}/api/v1/request/{request_id}/decline"
        client = await self._get_client()

        try:
            body = {"reason": reason} if reason else {}
            response = await client.post(endpoint, json=body)
            return response.status_code == 200
        except httpx.RequestError:
            return False

    async def get_request_status(self, request_id: int) -> dict | None:
        """Get request status from Overseerr API."""
        if not self.base_url or not self.api_key:
            return None

        endpoint = f"{self.base_url}/api/v1/request/{request_id}"
        client = await self._get_client()

        try:
            response = await client.get(endpoint)
            if response.status_code == 200:
                return response.json()
            return None
        except httpx.RequestError:
            return None
