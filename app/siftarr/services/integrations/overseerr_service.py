"""Service for interacting with Overseerr API."""

import time
from collections import OrderedDict
from typing import Any
from urllib.parse import quote

import httpx

from app.siftarr.config import Settings, get_settings
from app.siftarr.services.utils.http_client import get_shared_client

_MEDIA_DETAILS_CACHE: OrderedDict[tuple[str, int], tuple[float, dict]] = OrderedDict()
_MEDIA_DETAILS_CACHE_TTL = 60.0
_MEDIA_DETAILS_CACHE_MAX_SIZE = 512


def clear_media_details_cache() -> int:
    """Clear the app-side Overseerr media-details cache."""
    cleared_entries = len(_MEDIA_DETAILS_CACHE)
    _MEDIA_DETAILS_CACHE.clear()
    return cleared_entries


def extract_poster_path(poster_path: object) -> str | None:
    """Extract a clean TMDB poster path from various Overseerr response formats.

    Returns a TMDB-relative path like ``/abc123.jpg`` or *None*.
    """
    if not poster_path:
        return None

    poster = str(poster_path).strip()
    if not poster:
        return None

    # Already a bare TMDB path, e.g. "/kSf9svfD2WiLhrs9AP2Uih2Wq3T.jpg"
    if poster.startswith("/") and not poster.startswith("/images"):
        return poster

    # Overseerr proxied form: "/images/original/kSf9sv...jpg"
    if poster.startswith("/images/"):
        # Strip the /images/<size> prefix
        parts = poster.split("/", 3)  # ['', 'images', 'original', 'rest.jpg']
        if len(parts) >= 4:
            return f"/{parts[3]}"
        return None

    # Full URL pointing to TMDB
    if "image.tmdb.org" in poster:
        # e.g. https://image.tmdb.org/t/p/original/abc.jpg -> /abc.jpg
        idx = poster.find("/t/p/")
        if idx != -1:
            after = poster[idx + 4 :]  # "/original/abc.jpg"
            parts = after.split("/", 2)  # ['', 'original', 'abc.jpg']
            if len(parts) >= 3:
                return f"/{parts[2]}"
        return None

    # Full URL pointing to Overseerr instance – extract the TMDB portion
    if poster.startswith(("http://", "https://")) and "/images/" in poster:
        idx = poster.find("/images/")
        return extract_poster_path(poster[idx:])

    return None


def build_poster_url(poster_path: object) -> str | None:
    """Build a proxied poster URL that the browser can always reach."""
    tmdb_path = extract_poster_path(poster_path)
    if not tmdb_path:
        return None
    return f"/api/poster?path={quote(tmdb_path, safe='')}"


def build_overseerr_media_url(
    overseerr_url: str | None,
    media_type: str,
    tmdb_id: int | None,
) -> str | None:
    """Build an Overseerr media URL for movie or TV pages."""
    if not overseerr_url or not tmdb_id:
        return None
    return f"{str(overseerr_url).rstrip('/')}/{media_type}/{tmdb_id}"


class OverseerrFetchError(RuntimeError):
    """Raised when Overseerr data could not be fetched completely."""


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
        self.base_url = (self.settings.overseerr_url or "").rstrip("/")
        self.api_key = self.settings.overseerr_api_key

    @staticmethod
    def _normalize(status: Any, mapping: dict[int, str]) -> str:
        if isinstance(status, str):
            return status.lower()
        if isinstance(status, int):
            return mapping.get(status, f"unknown_{status}")
        return "unknown"

    @classmethod
    def normalize_media_status(cls, status: Any) -> str:
        """Normalize Overseerr media status to a string label."""
        return cls._normalize(status, cls.MEDIA_STATUS_MAP)

    @classmethod
    def normalize_request_status(cls, status: Any) -> str:
        """Normalize Overseerr request status to a string label."""
        return cls._normalize(status, cls.REQUEST_STATUS_MAP)

    def _get_headers(self) -> dict[str, str]:
        return {"X-Api-Key": self.api_key or ""}

    async def _fetch_requests_page(
        self,
        status: str | None,
        limit: int,
        skip: int,
    ) -> list[dict[str, Any]]:
        """Fetch one page of requests, raising OverseerrFetchError on any failure."""
        if not self.base_url or not self.api_key:
            raise OverseerrFetchError("Overseerr is not configured")

        endpoint = f"{self.base_url}/api/v1/request"
        client = await get_shared_client()
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
        except httpx.RequestError as exc:
            raise OverseerrFetchError(f"Overseerr request list failed: {exc}") from exc
        if response.status_code != 200:
            raise OverseerrFetchError(
                f"Overseerr request list returned HTTP {response.status_code}"
            )
        data = response.json()
        # Overseerr returns { "results": [...] } or just [...] depending on version
        if isinstance(data, dict) and "results" in data:
            return data["results"]
        if isinstance(data, dict) and "data" in data:
            return data["data"]
        if isinstance(data, list):
            return data
        raise OverseerrFetchError("Overseerr request list had an unexpected shape")

    async def get_all_requests(
        self,
        status: str | None = None,
        page_size: int = 100,
        max_pages: int = 50,
    ) -> list[dict[str, Any]]:
        """Fetch all requests across paginated Overseerr responses.

        Raises OverseerrFetchError if any page fails or the page limit is reached,
        so callers never mistake a partial list for the complete upstream state.
        """
        all_requests: list[dict[str, Any]] = []

        for page in range(max_pages):
            batch = await self._fetch_requests_page(
                status=status,
                limit=page_size,
                skip=page * page_size,
            )
            all_requests.extend(batch)
            if len(batch) < page_size:
                return all_requests

        raise OverseerrFetchError(f"Overseerr request list exceeded {max_pages} pages")

    @property
    def _configured(self) -> bool:
        return bool(self.base_url and self.api_key)

    async def _send(
        self,
        method: str,
        path: str,
        *,
        timeout: float | httpx.Timeout | None | Any = httpx.USE_CLIENT_DEFAULT,
        **kwargs: Any,
    ) -> httpx.Response | None:
        """Send an authenticated request. Return None if unconfigured or on network error."""
        if not self._configured:
            return None
        client = await get_shared_client()
        try:
            return await client.request(
                method,
                f"{self.base_url}/api/v1/{path}",
                headers=self._get_headers(),
                timeout=timeout,
                **kwargs,
            )
        except httpx.RequestError:
            return None

    async def _get_json(self, path: str, *, timeout: float | None = None) -> Any | None:
        """GET a path and return the decoded body on HTTP 200, else None."""
        kwargs: dict[str, Any] = {} if timeout is None else {"timeout": timeout}
        response = await self._send("GET", path, **kwargs)
        if response is None or response.status_code != 200:
            return None
        return response.json()

    async def _post_ok(self, path: str, json: dict[str, Any] | None = None) -> bool:
        """POST to a path and return True on HTTP 200, else False."""
        kwargs: dict[str, Any] = {} if json is None else {"json": json}
        response = await self._send("POST", path, **kwargs)
        return response is not None and response.status_code == 200

    async def request_exists(self, request_id: int) -> bool | None:
        """Return True/False if Overseerr confirms the request, or None if unknown."""
        response = await self._send("GET", f"request/{request_id}")
        if response is None:
            return None
        if response.status_code == 200:
            return True
        if response.status_code == 404:
            return False
        return None

    async def get_request(self, request_id: int) -> dict[str, Any] | None:
        """Get full request details from Overseerr.

        Args:
            request_id: The Overseerr request ID.

        Returns:
            A dict containing request details if successful, None otherwise.
        """
        return await self._get_json(f"request/{request_id}")

    async def get_media_details(self, media_type: str, external_id: int) -> dict | None:
        """Fetch media details from Overseerr.

        Args:
            media_type: The type of media ('movie' or 'tv').
            external_id: The TMDB ID for movies or TVDB ID for TV shows.

        Returns:
            A dict containing media details if successful, None otherwise.
        """
        if not self._configured:
            return None

        cache_key = (media_type, external_id)
        now = time.monotonic()
        expired_keys = [
            key
            for key, (timestamp, _) in _MEDIA_DETAILS_CACHE.items()
            if now - timestamp >= _MEDIA_DETAILS_CACHE_TTL
        ]
        for key in expired_keys:
            del _MEDIA_DETAILS_CACHE[key]
        cached = _MEDIA_DETAILS_CACHE.get(cache_key)
        if cached is not None:
            _MEDIA_DETAILS_CACHE.move_to_end(cache_key)
            return cached[1]

        data = await self._get_json(f"{media_type}/{external_id}", timeout=30.0)
        if data is None:
            return None
        _MEDIA_DETAILS_CACHE[cache_key] = (time.monotonic(), data)
        _MEDIA_DETAILS_CACHE.move_to_end(cache_key)
        while len(_MEDIA_DETAILS_CACHE) > _MEDIA_DETAILS_CACHE_MAX_SIZE:
            _MEDIA_DETAILS_CACHE.popitem(last=False)
        return data

    async def get_season_details(self, tv_id: int, season_number: int) -> dict | None:
        """Fetch season details (including episodes) from Overseerr.

        Args:
            tv_id: The TMDB ID for the TV show.
            season_number: The season number to fetch.

        Returns:
            A dict containing season details with episodes if successful, None otherwise.
        """
        return await self._get_json(f"tv/{tv_id}/season/{season_number}", timeout=30.0)

    async def decline_request(self, request_id: int, reason: str | None = None) -> bool:
        """Decline a request in Overseerr via API."""
        return await self._post_ok(
            f"request/{request_id}/decline", {"reason": reason} if reason else {}
        )

    async def approve_request(self, request_id: int) -> bool:
        """Approve a request in Overseerr via API."""
        return await self._post_ok(f"request/{request_id}/approve")
