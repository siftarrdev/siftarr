"""Service for interacting with Overseerr API."""

import time
from typing import Any

import httpx

from app.siftarr.config import Settings, get_settings
from app.siftarr.services.http_client import get_shared_client

_STATUS_CACHE: dict[int, tuple[float, dict]] = {}
_STATUS_CACHE_TTL = 60.0


class OverseerrService:
    """Service for fetching media details from Overseerr."""

    MEDIA_STATUS_MAP = {
        1: "unknown",
        2: "pending",
        3: "processing",
        4: "partially_available",
        5: "available",
        6: "deleted",
    }

    REQUEST_STATUS_MAP = {
        1: "pending",
        2: "approved",
        3: "declined",
        4: "failed",
        5: "completed",
    }

    def __init__(self, settings: Settings | None = None) -> None:
        """Initialize the Overseerr service."""
        self.settings = settings or get_settings()
        self.base_url = str(self.settings.overseerr_url).rstrip("/")
        self.api_key = self.settings.overseerr_api_key

    @classmethod
    def normalize_media_status(cls, status: Any) -> str:
        """Normalize Overseerr media status to a string label."""
        if isinstance(status, str):
            return status.lower()
        if isinstance(status, int):
            return cls.MEDIA_STATUS_MAP.get(status, f"unknown_{status}")
        return "unknown"

    @classmethod
    def normalize_request_status(cls, status: Any) -> str:
        """Normalize Overseerr request status to a string label."""
        if isinstance(status, str):
            return status.lower()
        if isinstance(status, int):
            return cls.REQUEST_STATUS_MAP.get(status, f"unknown_{status}")
        return "unknown"

    def _get_headers(self) -> dict[str, str]:
        api_key = self.api_key
        if api_key is None:
            api_key = ""
        return {"X-Api-Key": api_key}

    async def _get_client(self) -> httpx.AsyncClient:
        return await get_shared_client()

    async def close(self) -> None:
        pass

    async def get_requests(
        self,
        status: str | None = "approved",
        limit: int = 100,
        skip: int = 0,
    ) -> list[dict[str, Any]]:
        """
        Fetch requests from Overseerr.

        Args:
            status: Filter by request status ('approved', 'pending', etc.)
            limit: Maximum number of results to return
            skip: Number of results to skip for pagination

        Returns:
            List of request dictionaries from Overseerr API.
        """
        if not self.base_url or not self.api_key:
            return []

        endpoint = f"{self.base_url}/api/v1/request"
        client = await self._get_client()
        headers = self._get_headers()
        params: dict[str, Any] = {"take": limit, "skip": skip}
        if status and status != "all":
            params["filter"] = status

        try:
            response = await client.get(
                endpoint,
                headers=headers,
                params=params,
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

    async def get_all_requests(
        self,
        status: str | None = None,
        page_size: int = 100,
        max_pages: int = 20,
    ) -> list[dict[str, Any]]:
        """Fetch all requests across paginated Overseerr responses."""
        all_requests: list[dict[str, Any]] = []

        for page in range(max_pages):
            batch = await self.get_requests(
                status=status,
                limit=page_size,
                skip=page * page_size,
            )
            if not batch:
                break

            all_requests.extend(batch)
            if len(batch) < page_size:
                break

        return all_requests

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
        headers = self._get_headers()

        try:
            response = await client.get(endpoint, headers=headers)
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
        client = await self._get_client()
        headers = self._get_headers()

        try:
            response = await client.get(endpoint, headers=headers, timeout=30.0)
            if response.status_code == 200:
                return response.json()
            return None
        except httpx.RequestError:
            return None

    async def get_season_details(self, tv_id: int, season_number: int) -> dict | None:
        """Fetch season details (including episodes) from Overseerr.

        Args:
            tv_id: The TMDB ID for the TV show.
            season_number: The season number to fetch.

        Returns:
            A dict containing season details with episodes if successful, None otherwise.
        """
        if not self.base_url or not self.api_key:
            return None

        endpoint = f"{self.base_url}/api/v1/tv/{tv_id}/season/{season_number}"
        client = await self._get_client()
        headers = self._get_headers()

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
        headers = self._get_headers()

        try:
            response = await client.post(endpoint, headers=headers)
            return response.status_code == 200
        except httpx.RequestError:
            return False

    async def decline_request(self, request_id: int, reason: str | None = None) -> bool:
        """Decline a request in Overseerr via API."""
        if not self.base_url or not self.api_key:
            return False

        endpoint = f"{self.base_url}/api/v1/request/{request_id}/decline"
        client = await self._get_client()
        headers = self._get_headers()

        try:
            body = {"reason": reason} if reason else {}
            response = await client.post(endpoint, headers=headers, json=body)
            return response.status_code == 200
        except httpx.RequestError:
            return False

    async def get_request_status(self, request_id: int) -> dict | None:
        """Get request status from Overseerr API."""
        if not self.base_url or not self.api_key:
            return None

        endpoint = f"{self.base_url}/api/v1/request/{request_id}"
        client = await self._get_client()
        headers = self._get_headers()

        try:
            response = await client.get(endpoint, headers=headers)
            if response.status_code == 200:
                return response.json()
            return None
        except httpx.RequestError:
            return None

    async def get_request_status_cached(self, request_id: int) -> dict | None:
        """Get request status with a 60-second in-memory TTL cache."""
        now = time.monotonic()
        cached = _STATUS_CACHE.get(request_id)
        if cached is not None:
            ts, data = cached
            if now - ts < _STATUS_CACHE_TTL:
                return data

        data = await self.get_request_status(request_id)
        if data is not None:
            _STATUS_CACHE[request_id] = (now, data)
        return data
