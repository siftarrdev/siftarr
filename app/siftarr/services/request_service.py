"""Shared request-loading and validation helpers for router endpoints."""

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.siftarr.models.request import MediaType, RequestStatus
from app.siftarr.models.request import Request as RequestModel


async def load_request_or_404(db: AsyncSession, request_id: int) -> RequestModel:
    """Load a request or raise 404."""
    result = await db.execute(select(RequestModel).where(RequestModel.id == request_id))
    request = result.scalar_one_or_none()
    if not request:
        raise HTTPException(status_code=404, detail="Request not found")
    return request


def validate_tv_request(request: RequestModel) -> None:
    """Raise HTTPException 400 if the request is not a TV show."""
    if request.media_type != MediaType.TV:
        raise HTTPException(status_code=400, detail="Request is not a TV show")


def ensure_tvdb_id(request: RequestModel) -> int:
    """Raise HTTPException 400 if the request has no TVDB ID; return the ID otherwise."""
    if not request.tvdb_id:
        raise HTTPException(status_code=400, detail="No TVDB ID available")
    return request.tvdb_id


def selection_redirect_url(
    redirect_to: str | None,
    request: RequestModel,
    *,
    prefer_staged_view: bool = False,
) -> str:
    """Return a sensible redirect target after release actions."""
    if redirect_to:
        return redirect_to
    if prefer_staged_view:
        return "/?tab=staged"
    return "/?tab=pending" if request.status == RequestStatus.PENDING else "/?tab=active"


def bulk_redirect_url(redirect_to: str | None) -> str:
    """Return the target tab after a bulk action completes."""
    return redirect_to or "/?tab=pending"
