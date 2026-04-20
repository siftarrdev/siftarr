"""Settings router schemas."""

from pydantic import BaseModel


class ConnectionSettings(BaseModel):
    """Connection settings model."""

    overseerr_url: str | None = None
    overseerr_api_key: str | None = None
    prowlarr_url: str | None = None
    prowlarr_api_key: str | None = None
    qbittorrent_url: str | None = None
    qbittorrent_username: str | None = None
    qbittorrent_password: str | None = None
    tz: str = "UTC"


class ConnectionTestResponse(BaseModel):
    """Response model for connection test."""

    service: str
    success: bool
    message: str
    details: str | None = None
