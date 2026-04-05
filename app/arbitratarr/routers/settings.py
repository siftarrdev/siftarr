"""Settings page router for viewing and editing application settings."""

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.arbitratarr.config import get_settings
from app.arbitratarr.database import get_db
from app.arbitratarr.models.request import RequestStatus
from app.arbitratarr.models.settings import Settings
from app.arbitratarr.services.pending_queue_service import PendingQueueService
from app.arbitratarr.services.rule_service import RuleService

router = APIRouter(prefix="/settings", tags=["settings"])
templates = Jinja2Templates(directory="app/arbitratarr/templates")


@router.get("")
async def get_settings_page(
    request: Request,
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> templates.TemplateResponse:
    """Display settings page."""
    settings = get_settings()

    # Get staging mode setting
    result = await db.execute(
        select(Settings).where(Settings.key == "staging_mode_enabled"),
    )
    staging_setting = result.scalar_one_or_none()
    staging_enabled = staging_setting.value == "true" if staging_setting else False

    # Get pending queue count
    queue_service = PendingQueueService(db)
    ready = await queue_service.get_ready_for_retry()
    pending_count = len(ready)

    # Get request stats
    result = await db.execute(select(RequestStatus))
    all_statuses = list(result.scalars().all())

    total_requests = len(all_statuses)
    completed = sum(1 for s in all_statuses if s == RequestStatus.COMPLETED)
    pending = sum(1 for s in all_statuses if s == RequestStatus.PENDING)
    failed = sum(1 for s in all_statuses if s == RequestStatus.FAILED)

    return templates.TemplateResponse(
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
            "env": {
                "OVERSEERR_URL": str(settings.overseerr_url),
                "PROWLARR_URL": str(settings.prowlarr_url),
                "QBITTORRENT_URL": str(settings.qbittorrent_url),
                "TZ": settings.tz,
            },
        },
    )


@router.post("/staging")
async def toggle_staging_mode(
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> RedirectResponse:
    """Toggle staging mode."""
    result = await db.execute(
        select(Settings).where(Settings.key == "staging_mode_enabled"),
    )
    staging_setting = result.scalar_one_or_none()

    if staging_setting:
        staging_setting.value = "false" if staging_setting.value == "true" else "true"
    else:
        staging_setting = Settings(
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
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> templates.TemplateResponse:
    """Manually trigger retry of pending items."""
    from app.arbitratarr.main import scheduler_service

    if scheduler_service:
        count = await scheduler_service.trigger_retry_now()
        message = f"Retrying {count} pending items"
    else:
        message = "Scheduler not available"

    return templates.TemplateResponse(
        "settings.html",
        {
            "request": request,
            "message": message,
            "message_type": "success",
        },
    )


@router.post("/sync-overseerr")
async def sync_overseerr(request: Request) -> templates.TemplateResponse:
    """Sync with Overseerr for new requests."""
    # TODO: Implement Overseerr sync
    return templates.TemplateResponse(
        "settings.html",
        {
            "request": request,
            "message": "Overseerr sync not yet implemented",
            "message_type": "error",
        },
    )


@router.post("/reseed-rules")
async def reseed_rules(
    request: Request,
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> templates.TemplateResponse:
    """Reseed default rules."""
    rule_service = RuleService(db)
    await rule_service.seed_default_rules()

    return templates.TemplateResponse(
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
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> RedirectResponse:
    """Update size limit settings."""
    # TODO: Implement size limit settings
    return RedirectResponse(url="/settings", status_code=303)
