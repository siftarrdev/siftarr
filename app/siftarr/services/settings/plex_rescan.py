"""Plex rescan orchestration for settings routes."""

import asyncio
import contextlib
from typing import Any

from app.siftarr.config import get_settings
from app.siftarr.models.request import MediaType, RequestStatus
from app.siftarr.models.request import Request as RequestModel

from .sse import serialize_sse


async def rescan_plex_tv_request(
    request_id: int,
    plex,
    runtime_settings,
    *,
    session_maker,
    logger,
    force_plex_refresh: bool = True,
) -> bool:
    """Resync one TV request on an isolated DB session."""
    from app.siftarr.services.episode_sync_service import EpisodeSyncService
    from app.siftarr.services.overseerr_service import OverseerrService

    async with session_maker() as worker_db:
        overseerr = OverseerrService(settings=runtime_settings)
        episode_sync = EpisodeSyncService(worker_db, overseerr=overseerr, plex=plex)
        try:
            await episode_sync.sync_episodes(request_id, force_plex_refresh=force_plex_refresh)
        except Exception:
            await worker_db.rollback()
            logger.exception(
                "Plex TV resync failed for request_id=%s during settings rescan",
                request_id,
            )
            return False
        finally:
            await overseerr.close()

    return True


async def rescan_plex_requests(
    db,
    runtime_settings,
    plex,
    *,
    on_event=None,
    shallow: bool = False,
    plex_polling_service_cls,
    build_sse_progress_func,
    run_bounded_with_progress_func,
    rescan_plex_tv_request_func,
) -> tuple[int, int, int]:
    """Run the legacy manual Plex reconcile path."""
    polling_service = plex_polling_service_cls(db, plex)
    active_requests = await polling_service.get_active_requests()
    active_requests = [req for req in active_requests if req.status != RequestStatus.COMPLETED]
    tv_requests = [req for req in active_requests if req.media_type == MediaType.TV]

    if shallow:

        def all_episodes_available(req: RequestModel) -> bool:
            seasons = list(getattr(req, "seasons", []) or [])
            if not seasons:
                return False
            for season in seasons:
                episodes = list(getattr(season, "episodes", []) or [])
                if not episodes:
                    return False
                for episode in episodes:
                    if episode.status != RequestStatus.COMPLETED:
                        return False
            return True

        tv_requests = [req for req in tv_requests if not all_episodes_available(req)]

    configured_concurrency = getattr(runtime_settings, "plex_sync_concurrency", 1)
    sync_concurrency = (
        configured_concurrency
        if isinstance(configured_concurrency, int) and configured_concurrency > 0
        else 1
    )

    if on_event is not None:
        await on_event(
            build_sse_progress_func(
                "fetching",
                title="Fetching active Plex requests...",
                active=[req.title or f"Request #{req.id}" for req in active_requests[:16]],
            )
        )

    async def resync_worker(request: RequestModel) -> bool:
        return await rescan_plex_tv_request_func(
            request.id,
            plex,
            runtime_settings,
            force_plex_refresh=not shallow,
        )

    if tv_requests:
        resync_results = await run_bounded_with_progress_func(
            tv_requests,
            sync_concurrency,
            resync_worker,
            on_event=on_event or (lambda _payload: None),
            phase="processing",
        )
    else:
        resync_results = []

    tv_resynced = sum(1 for result in resync_results if result)
    tv_failed = len(resync_results) - tv_resynced

    if on_event is not None:
        await on_event(
            build_sse_progress_func("polling", title="Running Plex availability poll...")
        )

    completed = await polling_service.poll(on_progress=on_event)
    return tv_resynced, tv_failed, completed


async def rescan_plex_generator(
    *,
    shallow: bool = False,
    async_session_maker,
    plex_service_cls,
    rescan_plex_requests_func,
    build_sse_progress_func,
    logger,
):
    """Yield SSE events for Plex re-scan progress."""
    try:
        yield serialize_sse({"phase": "connecting"})

        async with async_session_maker() as db:
            runtime_settings = get_settings()
            plex = plex_service_cls(settings=runtime_settings)
            try:
                queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

                async def emit(payload: dict[str, Any]) -> None:
                    await queue.put(payload)

                task = asyncio.create_task(
                    rescan_plex_requests_func(
                        db,
                        runtime_settings,
                        plex,
                        on_event=emit,
                        shallow=shallow,
                    )
                )
                get_task = asyncio.create_task(queue.get())

                while True:
                    done, _pending = await asyncio.wait(
                        {task, get_task}, return_when=asyncio.FIRST_COMPLETED
                    )
                    if get_task in done:
                        payload = get_task.result()
                        if payload is not None:
                            yield serialize_sse(payload)
                        get_task = asyncio.create_task(queue.get())
                        continue

                    if task in done:
                        if not get_task.done():
                            get_task.cancel()
                            with contextlib.suppress(asyncio.CancelledError):
                                await get_task
                        while not queue.empty():
                            payload = queue.get_nowait()
                            if payload is not None:
                                yield serialize_sse(payload)
                        break

                resynced, failed, completed = await task
                yield serialize_sse(
                    build_sse_progress_func(
                        "complete",
                        resynced=resynced,
                        failed=failed,
                        completed=completed,
                        active=[],
                        message=(
                            "Legacy/manual Plex reconcile completed. "
                            f"Re-synced {resynced} TV request(s), "
                            f"{failed} failed, "
                            f"{completed} transitioned to completed."
                        ),
                    )
                )
            finally:
                await plex.close()

    except Exception as exc:
        logger.exception("Plex SSE re-scan failed")
        yield serialize_sse({"phase": "error", "message": f"Plex re-scan error: {exc}"})
