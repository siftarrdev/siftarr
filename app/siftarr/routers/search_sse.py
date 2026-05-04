"""SSE streaming endpoints for search and TV inspect operations."""

import logging

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.siftarr.database import get_db
from app.siftarr.routers.dashboard_actions import _process_request_search
from app.siftarr.services.dashboard_service import (
    DashboardService,
    serialize_tv_search_response,
)
from app.siftarr.services.request_service import load_request_or_404, validate_tv_request
from app.siftarr.services.settings_service import build_sse_progress, serialize_sse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/requests", tags=["search-sse"])


async def _search_request_generator(request_id: int, db: AsyncSession):
    try:
        request = await load_request_or_404(db, request_id)
        yield serialize_sse(
            build_sse_progress(
                "starting",
                percent=5,
                message=f"Starting search for {request.title}…",
            )
        )
        if request.year is None and (request.tmdb_id or request.tvdb_id):
            yield serialize_sse(
                build_sse_progress(
                    "backfilling",
                    percent=15,
                )
            )
        yield serialize_sse(
            build_sse_progress(
                "searching",
                percent=50,
                message="Querying indexers and evaluating releases…",
            )
        )
        result = await _process_request_search(request, db)
        yield serialize_sse(
            build_sse_progress(
                "complete",
                percent=100,
                message=result.get("message", "Search complete"),
                result=result,
            )
        )
    except Exception as exc:
        logger.exception("SSE search failed for request_id=%s", request_id)
        yield serialize_sse(
            build_sse_progress(
                "error",
                percent=100,
                message=str(exc),
            )
        )


async def _bulk_search_generator(request_ids: list[int], db: AsyncSession):
    try:
        total = len(request_ids)
        yield serialize_sse(
            build_sse_progress(
                "starting",
                percent=5,
                total=total,
            )
        )
        results: list[dict] = []
        for index, req_id in enumerate(request_ids):
            request = await load_request_or_404(db, req_id)
            percent = int(5 + ((index + 1) / total) * 90) if total else 5
            yield serialize_sse(
                build_sse_progress(
                    "searching",
                    percent=percent,
                    current=index + 1,
                    total=total,
                    title=request.title,
                )
            )
            result = await _process_request_search(request, db)
            results.append(
                {
                    "request_id": request.id,
                    "title": request.title,
                    **result,
                }
            )
        yield serialize_sse(
            build_sse_progress(
                "complete",
                percent=100,
                results=results,
            )
        )
    except Exception as exc:
        logger.exception("SSE bulk search failed")
        yield serialize_sse(
            build_sse_progress(
                "error",
                percent=100,
                message=str(exc),
            )
        )


async def _tv_season_pack_generator(request_id: int, season_number: int, db: AsyncSession):
    try:
        yield serialize_sse(build_sse_progress("starting", percent=5))
        request = await load_request_or_404(db, request_id)
        validate_tv_request(request)
        yield serialize_sse(build_sse_progress("searching", percent=50))
        service = DashboardService(db)
        data = await service.search_season_packs(request, season_number=season_number)
        serialized = serialize_tv_search_response(data)
        yield serialize_sse(
            build_sse_progress(
                "complete",
                percent=100,
                releases=serialized.get("releases", []),
                scope=serialized.get("scope"),
            )
        )
    except Exception as exc:
        logger.exception(
            "SSE season-pack search failed for request_id=%s season=%s",
            request_id,
            season_number,
        )
        yield serialize_sse(
            build_sse_progress(
                "error",
                percent=100,
                message=str(exc),
            )
        )


async def _tv_multi_season_generator(request_id: int, db: AsyncSession):
    try:
        yield serialize_sse(build_sse_progress("starting", percent=5))
        request = await load_request_or_404(db, request_id)
        validate_tv_request(request)
        yield serialize_sse(build_sse_progress("searching", percent=50))
        service = DashboardService(db)
        data = await service.search_multi_season_packs(request, request_id=request_id)
        serialized = serialize_tv_search_response(data)
        yield serialize_sse(
            build_sse_progress(
                "complete",
                percent=100,
                releases=serialized.get("releases", []),
                scope=serialized.get("scope"),
                known_total_seasons=serialized.get("known_total_seasons"),
            )
        )
    except Exception as exc:
        logger.exception("SSE multi-season search failed for request_id=%s", request_id)
        yield serialize_sse(
            build_sse_progress(
                "error",
                percent=100,
                message=str(exc),
            )
        )


async def _tv_episode_generator(
    request_id: int, season_number: int, episode_number: int, db: AsyncSession
):
    try:
        yield serialize_sse(build_sse_progress("starting", percent=5))
        request = await load_request_or_404(db, request_id)
        validate_tv_request(request)
        yield serialize_sse(build_sse_progress("searching", percent=50))
        service = DashboardService(db)
        data = await service.search_episode(
            request, season_number=season_number, episode_number=episode_number
        )
        serialized = serialize_tv_search_response(data)
        yield serialize_sse(
            build_sse_progress(
                "complete",
                percent=100,
                releases=serialized.get("releases", []),
                scope=serialized.get("scope"),
            )
        )
    except Exception as exc:
        logger.exception(
            "SSE episode search failed for request_id=%s season=%s episode=%s",
            request_id,
            season_number,
            episode_number,
        )
        yield serialize_sse(
            build_sse_progress(
                "error",
                percent=100,
                message=str(exc),
            )
        )


@router.get("/bulk/search/stream")
async def stream_bulk_search(
    request_ids: list[int] = Query(default=[]),
    db: AsyncSession = Depends(get_db),
):
    return StreamingResponse(
        _bulk_search_generator(request_ids, db),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/{request_id}/search/stream")
async def stream_search_request(
    request_id: int,
    db: AsyncSession = Depends(get_db),
):
    return StreamingResponse(
        _search_request_generator(request_id, db),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/{request_id}/seasons/{season_number}/season-packs/search/stream")
async def stream_tv_season_pack_search(
    request_id: int,
    season_number: int,
    db: AsyncSession = Depends(get_db),
):
    return StreamingResponse(
        _tv_season_pack_generator(request_id, season_number, db),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/{request_id}/multi-season-packs/search/stream")
async def stream_tv_multi_season_search(
    request_id: int,
    db: AsyncSession = Depends(get_db),
):
    return StreamingResponse(
        _tv_multi_season_generator(request_id, db),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/{request_id}/seasons/{season_number}/episodes/{episode_number}/search/stream")
async def stream_tv_episode_search(
    request_id: int,
    season_number: int,
    episode_number: int,
    db: AsyncSession = Depends(get_db),
):
    return StreamingResponse(
        _tv_episode_generator(request_id, season_number, episode_number, db),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
