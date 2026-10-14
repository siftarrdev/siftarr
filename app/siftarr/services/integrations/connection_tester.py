"""Service for testing connections to external services."""

from dataclasses import dataclass

import httpx

from app.siftarr.config import Settings
from app.siftarr.services.utils.http_client import get_shared_client


@dataclass
class ConnectionTestResult:
    """Result of a connection test."""

    success: bool
    message: str
    details: str | None = None


def _missing_config(
    label: str, url: object, secret: str | None, secret_name: str
) -> ConnectionTestResult | None:
    """Return a failure result when the URL or secret is not configured."""
    if not url:
        return ConnectionTestResult(success=False, message=f"{label} URL is not configured")
    if not secret:
        return ConnectionTestResult(
            success=False, message=f"{label} {secret_name} is not configured"
        )
    return None


class ConnectionTester:
    """Service for testing connections to external services."""

    @staticmethod
    async def _probe_http(
        label: str,
        endpoint: str,
        headers: dict[str, str],
        auth_detail: str,
        read_version: bool,
    ) -> ConnectionTestResult:
        """GET a status endpoint and map the outcome to a ConnectionTestResult."""
        try:
            client = await get_shared_client()
            response = await client.get(endpoint, headers=headers, timeout=10.0)
            if response.status_code == 200:
                details = None
                if read_version:
                    details = f"Version: {response.json().get('version', 'unknown')}"
                return ConnectionTestResult(
                    success=True,
                    message=f"Successfully connected to {label}",
                    details=details,
                )
            if response.status_code == 401:
                return ConnectionTestResult(
                    success=False,
                    message="Authentication failed",
                    details=auth_detail,
                )
            return ConnectionTestResult(
                success=False,
                message=f"HTTP Error: {response.status_code}",
            )
        except httpx.TimeoutException:
            return ConnectionTestResult(
                success=False,
                message="Connection timeout",
                details=f"{label} did not respond in time",
            )
        except httpx.RequestError as e:
            return ConnectionTestResult(
                success=False,
                message="Connection failed",
                details=str(e),
            )

    @staticmethod
    async def test_overseerr(settings: Settings) -> ConnectionTestResult:
        """Test connection to Overseerr.

        Args:
            settings: Application settings containing Overseerr configuration.

        Returns:
            ConnectionTestResult with success status and message.
        """
        if missing := _missing_config(
            "Overseerr", settings.overseerr_url, settings.overseerr_api_key, "API key"
        ):
            return missing

        endpoint = f"{str(settings.overseerr_url).rstrip('/')}/api/v1/status"
        headers = {"X-Api-Key": settings.overseerr_api_key}

        return await ConnectionTester._probe_http(
            "Overseerr", endpoint, headers, "Invalid API key", read_version=True
        )

    @staticmethod
    async def test_prowlarr(settings: Settings) -> ConnectionTestResult:
        """Test connection to Prowlarr.

        Args:
            settings: Application settings containing Prowlarr configuration.

        Returns:
            ConnectionTestResult with success status and message.
        """
        if missing := _missing_config(
            "Prowlarr", settings.prowlarr_url, settings.prowlarr_api_key, "API key"
        ):
            return missing

        endpoint = f"{str(settings.prowlarr_url).rstrip('/')}/api/v1/system/status"
        headers = {"X-Api-Key": settings.prowlarr_api_key}

        return await ConnectionTester._probe_http(
            "Prowlarr", endpoint, headers, "Invalid API key", read_version=True
        )

    @staticmethod
    async def test_qbittorrent(settings: Settings) -> ConnectionTestResult:
        """Test connection to qBittorrent.

        Args:
            settings: Application settings containing qBittorrent configuration.

        Returns:
            ConnectionTestResult with success status and message.
        """
        if missing := _missing_config(
            "qBittorrent", settings.qbittorrent_url, settings.qbittorrent_api_key, "API key"
        ):
            return missing

        try:
            import qbittorrentapi

            client = qbittorrentapi.Client(
                host=str(settings.qbittorrent_url),
                EXTRA_HEADERS={"Authorization": f"Bearer {settings.qbittorrent_api_key}"},
            )
            # API-key auth is sent with each request. Try to get version.
            try:
                version = client.app.web_api_version
                return ConnectionTestResult(
                    success=True,
                    message="Successfully connected to qBittorrent",
                    details=f"Web API Version: {version}",
                )
            except Exception:
                # Version check failed but login succeeded
                return ConnectionTestResult(
                    success=True,
                    message="Successfully connected to qBittorrent",
                    details="Connected (version check failed)",
                )
        except qbittorrentapi.LoginFailed:
            return ConnectionTestResult(
                success=False,
                message="Authentication failed",
                details="Invalid qBittorrent API key",
            )
        except Exception as e:
            return ConnectionTestResult(
                success=False,
                message="Connection failed",
                details=str(e),
            )

    @staticmethod
    async def test_plex(settings: Settings) -> ConnectionTestResult:
        """Test connection to Plex.

        Args:
            settings: Application settings containing Plex configuration.

        Returns:
            ConnectionTestResult with success status and message.
        """
        if missing := _missing_config("Plex", settings.plex_url, settings.plex_token, "token"):
            return missing

        endpoint = f"{str(settings.plex_url).rstrip('/')}/status"
        headers = {"X-Plex-Token": settings.plex_token, "Accept": "application/json"}

        return await ConnectionTester._probe_http(
            "Plex", endpoint, headers, "Invalid Plex token", read_version=False
        )
