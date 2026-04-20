"""Overseerr import orchestration for settings routes."""

import asyncio
import contextlib
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select

from app.siftarr.models.request import MediaType, RequestStatus
from app.siftarr.models.request import Request as RequestModel

from .sse import serialize_sse


@dataclass(slots=True)
class PreparedOverseerrImport:
    external_id: str
    media_type: MediaType
    tmdb_id: int | None
    tvdb_id: int | None
    title: str
    year: int | None
    requested_seasons: Any
    requested_episodes: Any
    requester_username: str | None
    requester_email: str | None
    overseerr_request_id: int | None
    media_details: dict | None


def extract_title_and_year_from_media_details(
    media_details: dict | None,
) -> tuple[str, int | None]:
    """Extract title and year from already-fetched Overseerr media details."""
    if not media_details:
        return "", None

    title = media_details.get("title") or media_details.get("name") or ""
    date_str = media_details.get("releaseDate") or media_details.get("firstAirDate") or ""
    year = None
    if date_str and len(date_str) >= 4:
        with contextlib.suppress(ValueError, TypeError):
            year = int(date_str[:4])
    return title, year


async def prepare_overseerr_import(
    ov_req: dict[str, Any],
    overseerr_service,
    semaphore: asyncio.Semaphore,
    media_details_tasks: dict[tuple[str, int], asyncio.Task[dict | None]],
    media_details_lock: asyncio.Lock,
) -> PreparedOverseerrImport | None:
    """Collect per-request metadata before serial DB writes."""
    media = ov_req.get("media") or {}
    tmdb_id = media.get("tmdbId")
    tvdb_id = media.get("tvdbId")
    overseerr_request_id = ov_req.get("id")

    if tmdb_id is None and tvdb_id is None:
        return None

    external_id = str(tmdb_id) if tmdb_id is not None else str(tvdb_id)
    media_type = MediaType.MOVIE if media.get("mediaType", "") == "movie" else MediaType.TV
    requested_seasons = media.get("requestedSeasons")
    requested_episodes = media.get("requestedEpisodes")

    requested_by = ov_req.get("requestedBy") or {}
    username = (
        requested_by.get("username")
        or requested_by.get("plexUsername")
        or requested_by.get("displayName")
    )
    email = requested_by.get("email")

    media_details = None
    media_external_id = tmdb_id if tmdb_id is not None else tvdb_id
    if media_external_id is not None:
        media_type_for_api = "movie" if media_type == MediaType.MOVIE else "tv"
        key = (media_type_for_api, media_external_id)

        async with media_details_lock:
            media_details_task = media_details_tasks.get(key)
            if media_details_task is None:

                async def fetch_media_details() -> dict | None:
                    async with semaphore:
                        return await overseerr_service.get_media_details(
                            media_type_for_api,
                            media_external_id,
                        )

                media_details_task = asyncio.create_task(fetch_media_details())
                media_details_tasks[key] = media_details_task

        media_details = await media_details_task

    title, year = extract_title_and_year_from_media_details(media_details)
    return PreparedOverseerrImport(
        external_id=external_id,
        media_type=media_type,
        tmdb_id=tmdb_id,
        tvdb_id=tvdb_id,
        title=title,
        year=year,
        requested_seasons=requested_seasons,
        requested_episodes=requested_episodes,
        requester_username=username,
        requester_email=email,
        overseerr_request_id=overseerr_request_id,
        media_details=media_details,
    )


async def import_overseerr_requests(
    db,
    runtime_settings,
    *,
    overseerr_service_cls,
    plex_service_cls,
    evaluate_imported_request_func,
    prepare_overseerr_import_func,
    logger,
) -> tuple[int, int]:
    """Fetch, filter, and import new Overseerr requests into the local DB."""
    overseerr_service = overseerr_service_cls(settings=runtime_settings)
    try:
        overseerr_requests = await overseerr_service.get_all_requests(status=None)
        if not overseerr_requests:
            return 0, 0

        result = await db.execute(select(RequestModel.external_id, RequestModel.overseerr_request_id))
        existing_rows = result.fetchall()
        existing_external_ids = {row[0] for row in existing_rows}
        existing_request_ids = {row[1] for row in existing_rows if row[1] is not None}

        actionable_requests = []
        for ov_req in overseerr_requests:
            media = ov_req.get("media") or {}
            request_status = overseerr_service.normalize_request_status(ov_req.get("status"))
            media_status = overseerr_service.normalize_media_status(media.get("status"))
            if request_status not in {"pending", "approved"}:
                continue
            if media_status == "available":
                continue
            actionable_requests.append(ov_req)

        sync_concurrency = max(1, runtime_settings.overseerr_sync_concurrency)
        sync_semaphore = asyncio.Semaphore(sync_concurrency)
        media_details_tasks: dict[tuple[str, int], asyncio.Task[dict | None]] = {}
        media_details_lock = asyncio.Lock()

        prepared_requests = await asyncio.gather(
            *(
                prepare_overseerr_import_func(
                    ov_req,
                    overseerr_service,
                    sync_semaphore,
                    media_details_tasks,
                    media_details_lock,
                )
                for ov_req in actionable_requests
            ),
            return_exceptions=True,
        )

        synced_count = 0
        skipped_count = 0
        new_tv_requests: list[RequestModel] = []

        for prepared_request in prepared_requests:
            try:
                if isinstance(prepared_request, BaseException):
                    logger.exception(
                        "Overseerr request prefetch failed during sync",
                        exc_info=prepared_request,
                    )
                    skipped_count += 1
                    continue
                if prepared_request is None:
                    skipped_count += 1
                    continue

                prepared = prepared_request
                if (
                    prepared.external_id in existing_external_ids
                    or prepared.overseerr_request_id in existing_request_ids
                ):
                    skipped_count += 1
                    continue

                new_request = RequestModel(
                    external_id=prepared.external_id,
                    media_type=prepared.media_type,
                    tmdb_id=prepared.tmdb_id,
                    tvdb_id=prepared.tvdb_id,
                    title=prepared.title,
                    year=prepared.year,
                    requested_seasons=str(prepared.requested_seasons)
                    if prepared.requested_seasons
                    else None,
                    requested_episodes=str(prepared.requested_episodes)
                    if prepared.requested_episodes
                    else None,
                    requester_username=prepared.requester_username,
                    requester_email=prepared.requester_email,
                    status=RequestStatus.PENDING,
                    overseerr_request_id=prepared.overseerr_request_id,
                )
                db.add(new_request)
                await db.flush()
                await evaluate_imported_request_func(
                    db,
                    overseerr_service,
                    new_request,
                    logger=logger,
                    prefetched_media_details=prepared.media_details,
                    local_episodes=(),
                )
                if prepared.media_type == MediaType.TV:
                    new_tv_requests.append(new_request)
                existing_external_ids.add(prepared.external_id)
                if prepared.overseerr_request_id is not None:
                    existing_request_ids.add(prepared.overseerr_request_id)
                synced_count += 1
            except Exception:
                logger.exception("Overseerr request import failed during sync")
                skipped_count += 1

        await db.commit()

        if synced_count > 0:
            from app.siftarr.services.episode_sync_service import EpisodeSyncService

            plex_service = plex_service_cls(settings=runtime_settings)
            try:
                episode_sync = EpisodeSyncService(db, overseerr=overseerr_service, plex=plex_service)
                for req in new_tv_requests:
                    try:
                        await episode_sync.sync_episodes(req.id)
                    except Exception:
                        logger.exception(
                            "Episode sync failed for request_id=%s during import",
                            req.id,
                        )
            finally:
                await plex_service.close()

        return synced_count, skipped_count
    finally:
        await overseerr_service.close()


async def sync_overseerr_generator(
    *,
    async_session_maker,
    build_effective_settings_func,
    get_effective_settings_func,
    import_overseerr_requests_func,
    build_sse_progress_func,
    logger,
):
    """Yield SSE events for Overseerr sync progress."""
    try:
        yield serialize_sse({"phase": "connecting"})

        async with async_session_maker() as db:
            effective_settings = await build_effective_settings_func(db)
            if not effective_settings.get("overseerr_url") or not effective_settings.get(
                "overseerr_api_key"
            ):
                yield serialize_sse(
                    {
                        "phase": "error",
                        "message": "Overseerr is not configured. Please set URL and API key.",
                    }
                )
                return

            runtime_settings = await get_effective_settings_func(db)
            yield serialize_sse(
                build_sse_progress_func(
                    "fetching",
                    title="Fetching requests from Overseerr...",
                    active=[],
                    message="Fetching requests from Overseerr...",
                )
            )

            synced_count, skipped_count = await import_overseerr_requests_func(db, runtime_settings)
            if synced_count > 0:
                message = f"Synced {synced_count} new request(s) from Overseerr"
            elif synced_count == 0 and skipped_count == 0:
                message = "No requests found in Overseerr"
            else:
                message = (
                    "No new actionable requests to sync "
                    f"({skipped_count} already existed or were already available)"
                )

            yield serialize_sse(
                build_sse_progress_func(
                    "complete",
                    active=[],
                    synced=synced_count,
                    skipped=skipped_count,
                    message=message,
                )
            )
    except Exception as exc:
        logger.exception("Overseerr SSE sync failed")
        yield serialize_sse(build_sse_progress_func("error", active=[], message=f"Sync error: {exc}"))
