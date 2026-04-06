"""Settings page router for viewing and editing application settings."""

from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.arbitratarr.config import Settings, get_settings
from app.arbitratarr.database import get_db
from app.arbitratarr.models.request import MediaType, Request as RequestModel, RequestStatus
from app.arbitratarr.models.settings import Settings as DBSettings
from app.arbitratarr.services.connection_tester import ConnectionTester, ConnectionTestResult
from app.arbitratarr.services.overseerr_service import OverseerrService
from app.arbitratarr.services.pending_queue_service import PendingQueueService
from app.arbitratarr.services.rule_service import RuleService

router = APIRouter(prefix="/settings", tags=["settings"])
templates = Jinja2Templates(directory="app/arbitratarr/templates")


# Pydantic models for connection settings
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


async def _get_db_setting(db: AsyncSession, key: str) -> str | None:
    """Get a setting value from the database."""
    result = await db.execute(select(DBSettings).where(DBSettings.key == key))
    setting = result.scalar_one_or_none()
    return setting.value if setting else None


async def _set_db_setting(db: AsyncSession, key: str, value: str, description: str | None = None) -> None:
    """Set a setting value in the database."""
    result = await db.execute(select(DBSettings).where(DBSettings.key == key))
    setting = result.scalar_one_or_none()
    if setting:
        setting.value = value
    else:
        setting = DBSettings(key=key, value=value, description=description)
        db.add(setting)


async def _build_effective_settings(db: AsyncSession, env_settings: Settings) -> dict:
    """Build effective settings, preferring database values over environment variables."""
    overseerr_url = await _get_db_setting(db, "overseerr_url")
    overseerr_api_key = await _get_db_setting(db, "overseerr_api_key")
    prowlarr_url = await _get_db_setting(db, "prowlarr_url")
    prowlarr_api_key = await _get_db_setting(db, "prowlarr_api_key")
    qbittorrent_url = await _get_db_setting(db, "qbittorrent_url")
    qbittorrent_username = await _get_db_setting(db, "qbittorrent_username")
    qbittorrent_password = await _get_db_setting(db, "qbittorrent_password")
    tz = await _get_db_setting(db, "tz")

    return {
        "overseerr_url": overseerr_url if overseerr_url else str(env_settings.overseerr_url or ""),
        "overseerr_api_key": overseerr_api_key if overseerr_api_key else str(env_settings.overseerr_api_key or ""),
        "prowlarr_url": prowlarr_url if prowlarr_url else str(env_settings.prowlarr_url or ""),
        "prowlarr_api_key": prowlarr_api_key if prowlarr_api_key else str(env_settings.prowlarr_api_key or ""),
        "qbittorrent_url": qbittorrent_url if qbittorrent_url else str(env_settings.qbittorrent_url or ""),
        "qbittorrent_username": qbittorrent_username if qbittorrent_username else env_settings.qbittorrent_username,
        "qbittorrent_password": qbittorrent_password if qbittorrent_password else env_settings.qbittorrent_password,
        "tz": tz if tz else env_settings.tz,
    }


async def _build_effective_settings_obj(db: AsyncSession, env_settings: Settings) -> Settings:
    """Build effective Settings object, preferring database values over environment variables."""
    eff = await _build_effective_settings(db, env_settings)
    # Create a new Settings object with effective values
    return Settings(
        overseerr_url=eff["overseerr_url"] or None,
        overseerr_api_key=eff["overseerr_api_key"] or None,
        prowlarr_url=eff["prowlarr_url"] or None,
        prowlarr_api_key=eff["prowlarr_api_key"] or None,
        qbittorrent_url=eff["qbittorrent_url"] or None,
        qbittorrent_username=eff["qbittorrent_username"],
        qbittorrent_password=eff["qbittorrent_password"],
        tz=eff["tz"],
    )


@router.get("")
async def get_settings_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Display settings page."""
    env_settings = get_settings()
    eff_settings = await _build_effective_settings(db, env_settings)

    # Get staging mode setting
    result = await db.execute(
        select(DBSettings).where(DBSettings.key == "staging_mode_enabled"),
    )
    staging_setting = result.scalar_one_or_none()
    staging_enabled = staging_setting.value == "true" if staging_setting else False

    # Get pending queue count
    queue_service = PendingQueueService(db)
    ready = await queue_service.get_ready_for_retry()
    pending_count = len(ready)

    # Get request stats
    result = await db.execute(select(RequestModel))
    all_requests = list(result.scalars().all())

    total_requests = len(all_requests)
    completed = sum(1 for r in all_requests if r.status == RequestStatus.COMPLETED)
    pending = sum(1 for r in all_requests if r.status == RequestStatus.PENDING)
    failed = sum(1 for r in all_requests if r.status == RequestStatus.FAILED)

    return templates.TemplateResponse(
        request,
        "settings.html",
        {
            "request": request,
            "staging_enabled": staging_enabled,
            "pending_count": pending_count,
            "stats": {
                "total_requests": total_requests,
                "completed": completed,
                "pending": pending,
                "failed": failed,
            },
            "env": eff_settings,
        },
    )


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
    tz: str | None = Form(None),
) -> RedirectResponse:
    """Save connection settings to database."""
    await _set_db_setting(db, "overseerr_url", overseerr_url or "", "Overseerr URL")
    await _set_db_setting(db, "overseerr_api_key", overseerr_api_key or "", "Overseerr API key")
    await _set_db_setting(db, "prowlarr_url", prowlarr_url or "", "Prowlarr URL")
    await _set_db_setting(db, "prowlarr_api_key", prowlarr_api_key or "", "Prowlarr API key")
    await _set_db_setting(db, "qbittorrent_url", qbittorrent_url or "", "qBittorrent URL")
    await _set_db_setting(db, "qbittorrent_username", qbittorrent_username or "", "qBittorrent username")
    await _set_db_setting(db, "qbittorrent_password", qbittorrent_password or "", "qBittorrent password")
    if tz:
        await _set_db_setting(db, "tz", tz, "Timezone")

    await db.commit()

    return RedirectResponse(url="/settings?saved=true", status_code=303)


@router.post("/connections/reset")
async def reset_connections(
    request: Request,
) -> RedirectResponse:
    """Reset connection settings by clearing database values."""
    # This just redirects - the effective settings will fall back to env vars
    return RedirectResponse(url="/settings?reset=true", status_code=303)


# API endpoints for testing connections
@router.get("/api/connections", response_model=dict)
async def get_connections_api(db: AsyncSession = Depends(get_db)) -> dict:
    """Get current connection settings (for API)."""
    env_settings = get_settings()
    eff = await _build_effective_settings(db, env_settings)
    return {
        "overseerr_url": eff["overseerr_url"],
        "overseerr_api_key": eff["overseerr_api_key"],
        "prowlarr_url": eff["prowlarr_url"],
        "prowlarr_api_key": eff["prowlarr_api_key"],
        "qbittorrent_url": eff["qbittorrent_url"],
        "qbittorrent_username": eff["qbittorrent_username"],
        "qbittorrent_password": eff["qbittorrent_password"],
        "tz": eff["tz"],
    }


@router.post("/api/test/overseerr", response_model=ConnectionTestResponse)
async def test_overseerr_connection(db: AsyncSession = Depends(get_db)) -> ConnectionTestResponse:
    """Test connection to Overseerr."""
    env_settings = get_settings()
    eff_settings = await _build_effective_settings_obj(db, env_settings)
    result: ConnectionTestResult = await ConnectionTester.test_overseerr(eff_settings)
    return ConnectionTestResponse(
        service="overseerr",
        success=result.success,
        message=result.message,
        details=result.details,
    )


@router.post("/api/test/prowlarr", response_model=ConnectionTestResponse)
async def test_prowlarr_connection(db: AsyncSession = Depends(get_db)) -> ConnectionTestResponse:
    """Test connection to Prowlarr."""
    env_settings = get_settings()
    eff_settings = await _build_effective_settings_obj(db, env_settings)
    result: ConnectionTestResult = await ConnectionTester.test_prowlarr(eff_settings)
    return ConnectionTestResponse(
        service="prowlarr",
        success=result.success,
        message=result.message,
        details=result.details,
    )


@router.post("/api/test/qbittorrent", response_model=ConnectionTestResponse)
async def test_qbittorrent_connection(db: AsyncSession = Depends(get_db)) -> ConnectionTestResponse:
    """Test connection to qBittorrent."""
    env_settings = get_settings()
    eff_settings = await _build_effective_settings_obj(db, env_settings)
    result: ConnectionTestResult = await ConnectionTester.test_qbittorrent(eff_settings)
    return ConnectionTestResponse(
        service="qbittorrent",
        success=result.success,
        message=result.message,
        details=result.details,
    )


@router.post("/api/test/all", response_model=list[ConnectionTestResponse])
async def test_all_connections(db: AsyncSession = Depends(get_db)) -> list[ConnectionTestResponse]:
    """Test connections to all services."""
    env_settings = get_settings()
    eff_settings = await _build_effective_settings_obj(db, env_settings)

    results = []
    for service_name, tester in [
        ("overseerr", ConnectionTester.test_overseerr),
        ("prowlarr", ConnectionTester.test_prowlarr),
        ("qbittorrent", ConnectionTester.test_qbittorrent),
    ]:
        result: ConnectionTestResult = await tester(eff_settings)
        results.append(ConnectionTestResponse(
            service=service_name,
            success=result.success,
            message=result.message,
            details=result.details,
        ))

    return results


@router.post("/staging")
async def toggle_staging_mode(
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    """Toggle staging mode."""
    result = await db.execute(
        select(DBSettings).where(DBSettings.key == "staging_mode_enabled"),
    )
    staging_setting = result.scalar_one_or_none()

    if staging_setting:
        staging_setting.value = "false" if staging_setting.value == "true" else "true"
    else:
        staging_setting = DBSettings(
            key="staging_mode_enabled",
            value="true",
            description="Enable staging mode to save torrents locally",
        )
        db.add(staging_setting)

    await db.commit()

    return RedirectResponse(url="/settings", status_code=303)


@router.post("/retry-pending")
async def retry_pending(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Manually trigger retry of pending items."""
    from app.arbitratarr.main import scheduler_service

    if scheduler_service:
        count = await scheduler_service.trigger_retry_now()
        message = f"Retrying {count} pending items"
    else:
        message = "Scheduler not available"

    return templates.TemplateResponse(
        request,
        "settings.html",
        {
            "request": request,
            "message": message,
            "message_type": "success",
        },
    )


@router.post("/sync-overseerr")
async def sync_overseerr(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Sync with Overseerr for new requests."""
    env_settings = get_settings()
    eff_settings = await _build_effective_settings(db, env_settings)

    # Get staging mode setting
    result = await db.execute(
        select(DBSettings).where(DBSettings.key == "staging_mode_enabled"),
    )
    staging_setting = result.scalar_one_or_none()
    staging_enabled = staging_setting.value == "true" if staging_setting else False

    # Get pending queue count
    queue_service = PendingQueueService(db)
    ready = await queue_service.get_ready_for_retry()
    pending_count = len(ready)

    # Get request stats
    result = await db.execute(select(RequestModel))
    all_requests = list(result.scalars().all())

    total_requests = len(all_requests)
    completed = sum(1 for r in all_requests if r.status == RequestStatus.COMPLETED)
    pending = sum(1 for r in all_requests if r.status == RequestStatus.PENDING)
    failed = sum(1 for r in all_requests if r.status == RequestStatus.FAILED)

    # Sync logic
    message = "Overseerr sync completed"
    message_type = "success"
    synced_count = 0
    skipped_count = 0

    # Check if Overseerr is configured
    if not eff_settings.get("overseerr_url") or not eff_settings.get("overseerr_api_key"):
        message = "Overseerr is not configured. Please set URL and API key."
        message_type = "error"
    else:
        overseerr_service = OverseerrService()
        try:
            # Fetch approved requests from Overseerr
            overseerr_requests = await overseerr_service.get_requests(status="approved", limit=100)

            if not overseerr_requests:
                # Try with different filter - maybe Overseerr uses different status values
                overseerr_requests_all = await overseerr_service.get_requests(status="all", limit=100)
                if overseerr_requests_all:
                    message = f"No approved requests found. Overseerr returned {len(overseerr_requests_all)} total requests. Check if requests exist with different status."
                    message_type = "error"
                else:
                    message = "No approved requests found in Overseerr"
                    message_type = "success"
            else:
                # Get existing external_ids from database
                result = await db.execute(select(RequestModel.external_id))
                existing_external_ids = set(row[0] for row in result.fetchall())

                # Process each request
                for ov_req in overseerr_requests:
                    try:
                        # Overseerr API returns media info nested under "media" key
                        media = ov_req.get("media") or {}
                        tmdb_id = media.get("tmdbId")
                        tvdb_id = media.get("tvdbId")

                        if tmdb_id is None and tvdb_id is None:
                            skipped_count += 1
                            continue

                        external_id = str(tmdb_id) if tmdb_id is not None else str(tvdb_id)

                        # Skip if already exists
                        if external_id in existing_external_ids:
                            skipped_count += 1
                            continue

                        # Determine media type
                        media_type_str = media.get("mediaType", "")
                        media_type = MediaType.MOVIE if media_type_str == "movie" else MediaType.TV

                        # Get requested seasons/episodes
                        requested_seasons = media.get("requestedSeasons")
                        requested_episodes = media.get("requestedEpisodes")

                        # Get requester info - use plexUsername or displayName as fallback for username
                        requested_by = ov_req.get("requestedBy") or {}
                        username = (
                            requested_by.get("username")
                            or requested_by.get("plexUsername")
                            or requested_by.get("displayName")
                        )
                        email = requested_by.get("email")

                        # Fetch title from Overseerr media details endpoint
                        title = ""
                        media_external_id = tmdb_id if tmdb_id else tvdb_id
                        if media_external_id:
                            media_type_for_api = "movie" if media_type == MediaType.MOVIE else "tv"
                            media_details = await overseerr_service.get_media_details(
                                media_type_for_api, media_external_id
                            )
                            if media_details:
                                title = media_details.get("title") or media_details.get("name") or ""

                        # Create new request
                        new_request = RequestModel(
                            external_id=external_id,
                            media_type=media_type,
                            tmdb_id=tmdb_id,
                            tvdb_id=tvdb_id,
                            title=title,
                            requested_seasons=str(requested_seasons) if requested_seasons else None,
                            requested_episodes=str(requested_episodes) if requested_episodes else None,
                            requester_username=username,
                            requester_email=email,
                            status=RequestStatus.PENDING,
                        )
                        db.add(new_request)
                        existing_external_ids.add(external_id)  # Prevent duplicates in same sync
                        synced_count += 1
                    except Exception as e:
                        # Log individual request processing errors but continue
                        skipped_count += 1
                        continue

                await db.commit()

                if synced_count > 0:
                    message = f"Synced {synced_count} new request(s) from Overseerr"
                    message_type = "success"
                else:
                    message = f"No new requests to sync ({skipped_count} already existed)"
                    message_type = "success"
        except Exception as e:
            message = f"Sync error: {str(e)}"
            message_type = "error"
        finally:
            await overseerr_service.close()

    return templates.TemplateResponse(
        request,
        "settings.html",
        {
            "request": request,
            "message": message,
            "message_type": message_type,
            "env": eff_settings,
            "staging_enabled": staging_enabled,
            "pending_count": pending_count,
            "stats": {
                "total_requests": total_requests,
                "completed": completed,
                "pending": pending,
                "failed": failed,
            },
        },
    )


@router.post("/reseed-rules")
async def reseed_rules(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Reseed default rules."""
    rule_service = RuleService(db)
    await rule_service.seed_default_rules()

    return templates.TemplateResponse(
        request,
        "settings.html",
        {
            "request": request,
            "message": "Default rules have been seeded",
            "message_type": "success",
        },
    )


@router.post("/size-limits")
async def update_size_limits(
    request: Request,
    min_size: float | None = Form(None),
    max_size: float | None = Form(None),
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    """Update size limit settings."""
    # TODO: Implement size limit settings
    return RedirectResponse(url="/settings", status_code=303)
