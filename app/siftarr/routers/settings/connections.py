"""Settings connection handlers."""

import sys

from fastapi import Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.siftarr.database import get_db
from app.siftarr.services.connection_tester import ConnectionTestResult

from .schemas import ConnectionTestResponse
from .shared import router

settings_router = sys.modules[__package__]


@router.post("/connections")
async def save_connections(
    request: Request,
    db: AsyncSession = Depends(get_db),
    overseerr_url: str | None = Form(None),
    overseerr_api_key: str | None = Form(None),
    prowlarr_url: str | None = Form(None),
    prowlarr_api_key: str | None = Form(None),
    qbittorrent_url: str | None = Form(None),
    qbittorrent_username: str | None = Form(None),
    qbittorrent_password: str | None = Form(None),
    plex_url: str | None = Form(None),
    plex_token: str | None = Form(None),
    tz: str | None = Form(None),
) -> RedirectResponse:
    """Save connection settings to database."""
    del request
    await settings_router._set_db_setting(db, "overseerr_url", overseerr_url or "", "Overseerr URL")
    await settings_router._set_db_setting(
        db,
        "overseerr_api_key",
        overseerr_api_key or "",
        "Overseerr API key",
    )
    await settings_router._set_db_setting(db, "prowlarr_url", prowlarr_url or "", "Prowlarr URL")
    await settings_router._set_db_setting(
        db,
        "prowlarr_api_key",
        prowlarr_api_key or "",
        "Prowlarr API key",
    )
    await settings_router._set_db_setting(
        db,
        "qbittorrent_url",
        qbittorrent_url or "",
        "qBittorrent URL",
    )
    await settings_router._set_db_setting(
        db,
        "qbittorrent_username",
        qbittorrent_username or "",
        "qBittorrent username",
    )
    await settings_router._set_db_setting(
        db,
        "qbittorrent_password",
        qbittorrent_password or "",
        "qBittorrent password",
    )
    await settings_router._set_db_setting(db, "plex_url", plex_url or "", "Plex URL")
    await settings_router._set_db_setting(db, "plex_token", plex_token or "", "Plex token")
    if tz:
        await settings_router._set_db_setting(db, "tz", tz, "Timezone")
    await db.commit()
    return RedirectResponse(url="/settings?saved=true", status_code=303)


@router.post("/connections/reset")
async def reset_connections(request: Request) -> RedirectResponse:
    """Reset connection settings by clearing database values."""
    del request
    return RedirectResponse(url="/settings?reset=true", status_code=303)


@router.get("/api/connections", response_model=dict)
async def get_connections_api(db: AsyncSession = Depends(get_db)) -> dict:
    """Get current connection settings (for API)."""
    effective = await settings_router._build_effective_settings(db)
    return {
        "overseerr_url": effective["overseerr_url"],
        "overseerr_api_key": effective["overseerr_api_key"],
        "prowlarr_url": effective["prowlarr_url"],
        "prowlarr_api_key": effective["prowlarr_api_key"],
        "qbittorrent_url": effective["qbittorrent_url"],
        "qbittorrent_username": effective["qbittorrent_username"],
        "qbittorrent_password": effective["qbittorrent_password"],
        "tz": effective["tz"],
    }


@router.post("/api/test/overseerr", response_model=ConnectionTestResponse)
async def test_overseerr_connection(db: AsyncSession = Depends(get_db)) -> ConnectionTestResponse:
    """Test connection to Overseerr."""
    effective_settings = await settings_router._build_effective_settings_obj(db)
    result: ConnectionTestResult = await settings_router.ConnectionTester.test_overseerr(
        effective_settings
    )
    return ConnectionTestResponse(
        service="overseerr",
        success=result.success,
        message=result.message,
        details=result.details,
    )


@router.post("/api/test/prowlarr", response_model=ConnectionTestResponse)
async def test_prowlarr_connection(db: AsyncSession = Depends(get_db)) -> ConnectionTestResponse:
    """Test connection to Prowlarr."""
    effective_settings = await settings_router._build_effective_settings_obj(db)
    result: ConnectionTestResult = await settings_router.ConnectionTester.test_prowlarr(
        effective_settings
    )
    return ConnectionTestResponse(
        service="prowlarr",
        success=result.success,
        message=result.message,
        details=result.details,
    )


@router.post("/api/test/qbittorrent", response_model=ConnectionTestResponse)
async def test_qbittorrent_connection(db: AsyncSession = Depends(get_db)) -> ConnectionTestResponse:
    """Test connection to qBittorrent."""
    effective_settings = await settings_router._build_effective_settings_obj(db)
    result: ConnectionTestResult = await settings_router.ConnectionTester.test_qbittorrent(
        effective_settings
    )
    return ConnectionTestResponse(
        service="qbittorrent",
        success=result.success,
        message=result.message,
        details=result.details,
    )


@router.post("/api/test/plex", response_model=ConnectionTestResponse)
async def test_plex_connection(db: AsyncSession = Depends(get_db)) -> ConnectionTestResponse:
    """Test connection to Plex."""
    effective_settings = await settings_router._build_effective_settings_obj(db)
    result: ConnectionTestResult = await settings_router.ConnectionTester.test_plex(effective_settings)
    return ConnectionTestResponse(
        service="plex",
        success=result.success,
        message=result.message,
        details=result.details,
    )


@router.post("/api/test/all", response_model=list[ConnectionTestResponse])
async def test_all_connections(db: AsyncSession = Depends(get_db)) -> list[ConnectionTestResponse]:
    """Test connections to all services."""
    effective_settings = await settings_router._build_effective_settings_obj(db)
    results = []
    for service_name, tester in [
        ("overseerr", settings_router.ConnectionTester.test_overseerr),
        ("prowlarr", settings_router.ConnectionTester.test_prowlarr),
        ("qbittorrent", settings_router.ConnectionTester.test_qbittorrent),
        ("plex", settings_router.ConnectionTester.test_plex),
    ]:
        result: ConnectionTestResult = await tester(effective_settings)
        results.append(
            ConnectionTestResponse(
                service=service_name,
                success=result.success,
                message=result.message,
                details=result.details,
            )
        )
    return results
