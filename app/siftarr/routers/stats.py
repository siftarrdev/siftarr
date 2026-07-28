"""Stats page and JSON endpoints."""

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from app.siftarr.config import get_static_version
from app.siftarr.database import get_db
from app.siftarr.services.stats_service import StatsRangeError, StatsService, build_stats_range
from app.siftarr.templating import configure_templates

router = APIRouter(prefix="/stats", tags=["stats"])
templates = configure_templates(Jinja2Templates(directory="app/siftarr/templates"))
templates.env.cache = None


def _range_from_query(range_key: str, start: str | None, end: str | None):
    try:
        return build_stats_range(range_key, start=start, end=end)
    except StatsRangeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("", response_class=HTMLResponse)
async def stats_page(request: Request) -> HTMLResponse:
    """Render the protected Stats tab."""
    return templates.TemplateResponse(
        request,
        "stats.html",
        {
            "request": request,
            "static_version": get_static_version(),
        },
    )


@router.get("/data")
async def stats_data(
    range_key: str = Query("30d", alias="range"),
    start: str | None = Query(None),
    end: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Return chart-ready Stats data."""
    stats_range = _range_from_query(range_key, start, end)
    return await StatsService(db).get_stats(stats_range)
