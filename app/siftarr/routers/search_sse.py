"""SSE streaming endpoints for search and TV inspect operations."""

import asyncio
import logging

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.siftarr import database
from app.siftarr.database import get_db
from app.siftarr.models.request import MediaType
from app.siftarr.models.request import Request as RequestModel
from app.siftarr.routers.dashboard_actions import _load_all_pending_search_requests
from app.siftarr.services.admin.settings_service import build_sse_progress, serialize_sse
from app.siftarr.services.dashboard.dashboard_service import (
    serialize_tv_search_response,
)
from app.siftarr.services.dashboard.search_service import SearchService
from app.siftarr.services.request_service import load_request_or_404, validate_tv_request

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/requests", tags=["search-sse"])


async def _new_detached_session() -> AsyncSession:
    if database.async_session_maker is None:
        database.init_engine()
    assert database.async_session_maker is not None
    return database.async_session_maker()


async def _run_request_search_detached(
    request_id: int,
    fallback_db: AsyncSession | None = None,
    progress_callback=None,
) -> dict:
    if database.async_session_maker is None and fallback_db is not None:
        request = await load_request_or_404(fallback_db, request_id)
        service = SearchService(fallback_db)
        result = await service.process_request_search(request, progress_callback=progress_callback)
        return {
            "request_id": request_id,
            "title": request.title,
            "media_type": request.media_type.value,
            **result,
        }

    async with await _new_detached_session() as db:
        request = await load_request_or_404(db, request_id)
        service = SearchService(db)
        result = await service.process_request_search(request, progress_callback=progress_callback)
        await db.commit()
        return {
            "request_id": request_id,
            "title": request.title,
            "media_type": request.media_type.value,
            **result,
        }


async def _run_bulk_search_detached(
    request_ids: list[int],
    *,
    search_all_pending: bool = False,
    fallback_db: AsyncSession | None = None,
    fallback_request_items: list[tuple[int | None, RequestModel | None]] | None = None,
) -> list[dict]:
    if database.async_session_maker is None and fallback_db is not None:
        return await _run_bulk_search_with_session(
            request_ids,
            fallback_db,
            search_all_pending=search_all_pending,
            commit_each=False,
            request_items=fallback_request_items,
        )

    async with await _new_detached_session() as db:
        return await _run_bulk_search_with_session(
            request_ids, db, search_all_pending=search_all_pending, commit_each=True
        )


async def _run_bulk_search_with_session(
    request_ids: list[int],
    db: AsyncSession,
    *,
    search_all_pending: bool = False,
    commit_each: bool = True,
    request_items: list[tuple[int | None, RequestModel | None]] | None = None,
) -> list[dict]:
    if request_items is not None:
        pass
    elif search_all_pending is True:
        requests = await _load_all_pending_search_requests(db)
        request_items: list[tuple[int | None, RequestModel | None]] = [
            (request.id, request) for request in requests
        ]
    else:
        request_items = [(req_id, None) for req_id in request_ids]

    results: list[dict] = []
    for req_id, loaded_request in request_items:
        try:
            request = loaded_request or await load_request_or_404(db, req_id or 0)
            service = SearchService(db)
            result = await service.process_request_search(request)
            if commit_each:
                await db.commit()
        except Exception as exc:
            logger.exception("Detached bulk search failed for request_id=%s", req_id)
            result = {"status": "failed", "message": str(exc)}
            request = loaded_request
            if commit_each:
                await db.rollback()
        results.append(
            {
                "request_id": request.id if request else req_id,
                "title": request.title if request else None,
                **result,
            }
        )
    return results


async def _search_request_generator(request_id: int, db: AsyncSession):
    try:
        request = await load_request_or_404(db, request_id)
        progress_queue: asyncio.Queue[dict] = asyncio.Queue()

        async def progress_callback(payload: dict) -> None:
            await progress_queue.put(payload)

        search_task = asyncio.create_task(
            _run_request_search_detached(
                request_id,
                db,
                progress_callback=progress_callback if request.media_type == MediaType.TV else None,
            )
        )
        if request.media_type == MediaType.TV:
            logger.info(
                "TV Search All stream started: request_id=%s title=%s source=prowlarr",
                request_id,
                request.title,
            )
        if request.media_type != MediaType.TV:
            yield serialize_sse(
                build_sse_progress(
                    "starting",
                    percent=5,
                    message=f"Starting search for {request.title}…",
                )
            )
        if (
            request.media_type != MediaType.TV
            and request.year is None
            and (request.tmdb_id or request.tvdb_id)
        ):
            yield serialize_sse(
                build_sse_progress(
                    "backfilling",
                    percent=15,
                )
            )
        if request.media_type != MediaType.TV:
            yield serialize_sse(
                build_sse_progress(
                    "searching",
                    percent=50,
                    message="Querying indexers and evaluating releases…",
                )
            )

        if request.media_type == MediaType.TV:
            while not search_task.done():
                try:
                    payload = await asyncio.wait_for(progress_queue.get(), timeout=0.1)
                except TimeoutError:
                    continue
                yield serialize_sse(payload)
            while not progress_queue.empty():
                yield serialize_sse(progress_queue.get_nowait())

        result = await asyncio.shield(search_task)
        if request.media_type == MediaType.TV:
            complete_message = (
                "TV Search All complete: evaluated releases, applied auto-stage/select rules, "
                "and refreshed DB-backed buckets."
            )
            logger.info(
                "TV Search All stream done: request_id=%s title=%s status=%s source=prowlarr",
                request_id,
                request.title,
                result.get("status"),
            )
        else:
            complete_message = result.get("message", "Search complete")
        if request.media_type == MediaType.TV:
            yield serialize_sse(
                build_sse_progress(
                    "complete",
                    percent=100,
                    message=complete_message,
                    result=result,
                    reload_details=True,
                    buckets_source="db",
                )
            )
            return
        yield serialize_sse(
            build_sse_progress(
                "complete",
                percent=100,
                message=complete_message,
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


async def _bulk_search_generator(
    request_ids: list[int], db: AsyncSession, *, search_all_pending: bool = False
):
    try:
        if search_all_pending is True:
            requests = await _load_all_pending_search_requests(db)
            request_items: list[tuple[int | None, RequestModel | None]] = [
                (request.id, request) for request in requests
            ]
        else:
            request_items: list[tuple[int | None, RequestModel | None]] = [
                (req_id, None) for req_id in request_ids
            ]

        total = len(request_items)
        search_task = asyncio.create_task(
            _run_bulk_search_detached(
                request_ids,
                search_all_pending=search_all_pending,
                fallback_db=db,
                fallback_request_items=request_items,
            )
        )
        yield serialize_sse(
            build_sse_progress(
                "starting",
                percent=5,
                total=total,
            )
        )
        for index, (_req_id, loaded_request) in enumerate(request_items):
            percent = int(5 + ((index + 1) / total) * 90) if total else 5
            yield serialize_sse(
                build_sse_progress(
                    "searching",
                    percent=percent,
                    current=index + 1,
                    total=total,
                    title=loaded_request.title if loaded_request else None,
                )
            )
        results = await asyncio.shield(search_task)
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
    """Compatibility/debug stream for inspecting cached/refreshed season-pack rows."""
    try:
        yield serialize_sse(build_sse_progress("starting", percent=5))
        request = await load_request_or_404(db, request_id)
        validate_tv_request(request)
        yield serialize_sse(build_sse_progress("searching", percent=50))
        service = SearchService(db)
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
    """Compatibility/debug stream for inspecting cached/refreshed multi-season rows."""
    try:
        yield serialize_sse(build_sse_progress("starting", percent=5))
        request = await load_request_or_404(db, request_id)
        validate_tv_request(request)
        yield serialize_sse(build_sse_progress("searching", percent=50))
        service = SearchService(db)
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
    """Compatibility/debug stream for inspecting cached/refreshed episode rows."""
    try:
        yield serialize_sse(build_sse_progress("starting", percent=5))
        request = await load_request_or_404(db, request_id)
        validate_tv_request(request)
        yield serialize_sse(build_sse_progress("searching", percent=50))
        service = SearchService(db)
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
    search_all_pending: bool = Query(default=False),
    db: AsyncSession = Depends(get_db),
):
    return StreamingResponse(
        _bulk_search_generator(request_ids, db, search_all_pending=search_all_pending),
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
    """Primary dashboard search stream; TV requests use this as Search All."""
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
    """Compatibility/debug SSE endpoint; normal TV UX uses /{id}/search/stream."""
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
    """Compatibility/debug SSE endpoint; normal TV UX uses /{id}/search/stream."""
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
    """Compatibility/debug SSE endpoint; normal TV UX uses /{id}/search/stream."""
    return StreamingResponse(
        _tv_episode_generator(request_id, season_number, episode_number, db),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
