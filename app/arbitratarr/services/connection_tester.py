"""Service for testing connections to external services."""

import httpx

from app.arbitratarr.config import Settings


class ConnectionTestResult:
    """Result of a connection test."""

    def __init__(self, success: bool, message: str, details: str | None = None):
        self.success = success
        self.message = message
        self.details = details


class ConnectionTester:
    """Service for testing connections to external services."""

    @staticmethod
    async def test_overseerr(settings: Settings) -> ConnectionTestResult:
        """Test connection to Overseerr.

        Args:
            settings: Application settings containing Overseerr configuration.

        Returns:
            ConnectionTestResult with success status and message.
        """
        if not settings.overseerr_url:
            return ConnectionTestResult(
                success=False,
                message="Overseerr URL is not configured",
            )

        if not settings.overseerr_api_key:
            return ConnectionTestResult(
                success=False,
                message="Overseerr API key is not configured",
            )

        endpoint = f"{str(settings.overseerr_url).rstrip('/')}/api/v1/status"
        headers = {"X-Api-Key": settings.overseerr_api_key}

        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(endpoint, headers=headers, timeout=10.0)
                if response.status_code == 200:
                    data = response.json()
                    version = data.get("version", "unknown")
                    return ConnectionTestResult(
                        success=True,
                        message="Successfully connected to Overseerr",
                        details=f"Version: {version}",
                    )
                elif response.status_code == 401:
                    return ConnectionTestResult(
                        success=False,
                        message="Authentication failed",
                        details="Invalid API key",
                    )
                else:
                    return ConnectionTestResult(
                        success=False,
                        message=f"HTTP Error: {response.status_code}",
                    )
        except httpx.TimeoutException:
            return ConnectionTestResult(
                success=False,
                message="Connection timeout",
                details="Overseerr did not respond in time",
            )
        except httpx.RequestError as e:
            return ConnectionTestResult(
                success=False,
                message="Connection failed",
                details=str(e),
            )

    @staticmethod
    async def test_prowlarr(settings: Settings) -> ConnectionTestResult:
        """Test connection to Prowlarr.

        Args:
            settings: Application settings containing Prowlarr configuration.

        Returns:
            ConnectionTestResult with success status and message.
        """
        if not settings.prowlarr_url:
            return ConnectionTestResult(
                success=False,
                message="Prowlarr URL is not configured",
            )

        if not settings.prowlarr_api_key:
            return ConnectionTestResult(
                success=False,
                message="Prowlarr API key is not configured",
            )

        endpoint = f"{str(settings.prowlarr_url).rstrip('/')}/api/v1/system/status"
        headers = {"X-Api-Key": settings.prowlarr_api_key}

        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(endpoint, headers=headers, timeout=10.0)
                if response.status_code == 200:
                    data = response.json()
                    version = data.get("version", "unknown")
                    return ConnectionTestResult(
                        success=True,
                        message="Successfully connected to Prowlarr",
                        details=f"Version: {version}",
                    )
                elif response.status_code == 401:
                    return ConnectionTestResult(
                        success=False,
                        message="Authentication failed",
                        details="Invalid API key",
                    )
                else:
                    return ConnectionTestResult(
                        success=False,
                        message=f"HTTP Error: {response.status_code}",
                    )
        except httpx.TimeoutException:
            return ConnectionTestResult(
                success=False,
                message="Connection timeout",
                details="Prowlarr did not respond in time",
            )
        except httpx.RequestError as e:
            return ConnectionTestResult(
                success=False,
                message="Connection failed",
                details=str(e),
            )

    @staticmethod
    async def test_qbittorrent(settings: Settings) -> ConnectionTestResult:
        """Test connection to qBittorrent.

        Args:
            settings: Application settings containing qBittorrent configuration.

        Returns:
            ConnectionTestResult with success status and message.
        """
        if not settings.qbittorrent_url:
            return ConnectionTestResult(
                success=False,
                message="qBittorrent URL is not configured",
            )

        if not settings.qbittorrent_username:
            return ConnectionTestResult(
                success=False,
                message="qBittorrent username is not configured",
            )

        if not settings.qbittorrent_password:
            return ConnectionTestResult(
                success=False,
                message="qBittorrent password is not configured",
            )

        try:
            import asyncio

            import qbittorrentapi

            client = qbittorrentapi.Client(
                host=str(settings.qbittorrent_url),
                username=settings.qbittorrent_username,
                password=settings.qbittorrent_password,
            )
            # Attempt to log in synchronously
            await asyncio.to_thread(client.auth.log_in)
            # If we get here, login succeeded - now try to get version
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
                details="Invalid username or password",
            )
        except Exception as e:
            return ConnectionTestResult(
                success=False,
                message="Connection failed",
                details=str(e),
            )
