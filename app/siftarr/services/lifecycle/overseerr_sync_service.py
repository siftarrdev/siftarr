"""Best-effort Overseerr lifecycle sync helpers."""

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.siftarr.config import get_settings
from app.siftarr.models.activity_log import EventType
from app.siftarr.models.request import Request
from app.siftarr.services.integrations.overseerr_service import OverseerrService
from app.siftarr.services.lifecycle.activity_log_service import ActivityLogService

logger = logging.getLogger(__name__)


async def approve_overseerr_request_best_effort(
    db: AsyncSession,
    request: Request | None,
    *,
    reason: str,
) -> bool:
    """Approve a linked Overseerr request without blocking local lifecycle work."""
    overseerr_request_id = getattr(request, "overseerr_request_id", None) if request else None
    if request is None or not isinstance(overseerr_request_id, int) or overseerr_request_id <= 0:
        return False

    try:
        approved = await OverseerrService(settings=get_settings()).approve_request(
            overseerr_request_id
        )
    except Exception:
        logger.exception(
            "Overseerr approval sync failed for request_id=%s overseerr_request_id=%s",
            request.id,
            overseerr_request_id,
        )
        await ActivityLogService(db).log(
            EventType.ERROR,
            request_id=request.id,
            details={"action": "overseerr_approve", "reason": reason, "status": "exception"},
        )
        return False

    if approved:
        logger.info(
            "Overseerr approval synced for request_id=%s overseerr_request_id=%s (%s)",
            request.id,
            overseerr_request_id,
            reason,
        )
        await ActivityLogService(db).log(
            EventType.REQUEST_STATUS_CHANGED,
            request_id=request.id,
            details={"action": "overseerr_approve", "reason": reason, "status": "ok"},
        )
        return True

    logger.warning(
        "Overseerr approval sync was not accepted for request_id=%s overseerr_request_id=%s",
        request.id,
        overseerr_request_id,
    )
    await ActivityLogService(db).log(
        EventType.ERROR,
        request_id=request.id,
        details={"action": "overseerr_approve", "reason": reason, "status": "failed"},
    )
    return False
