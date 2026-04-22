"""Dashboard JSON API router for request details, search, and state mutations."""

import asyncio
import json
import logging
from collections.abc import Mapping
from typing import Any, cast

from fastapi import APIRouter, BackgroundTasks, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.siftarr.config import get_settings
from app.siftarr.database import get_db
from app.siftarr.models.request import MediaType, RequestStatus, is_active_staging_workflow_status
from app.siftarr.models.rule import Rule
from app.siftarr.models.staged_torrent import StagedTorrent
from app.siftarr.routers.dashboard_actions import _process_request_search
from app.siftarr.services.activity_log_service import ActivityLogService
from app.siftarr.services.overseerr_service import (
    OverseerrService,
    build_overseerr_media_url,
    build_poster_url,
)
from app.siftarr.services.plex_service import PlexService
from app.siftarr.services.prowlarr_service import ProwlarrService
from app.siftarr.services.release_parser import (
    is_exact_single_episode_release,
    parse_release_coverage,
    parse_stored_release_coverage,
)
from app.siftarr.services.release_storage import build_prowlarr_release
from app.siftarr.services.release_serializers import (
    apply_release_size_per_season_metadata,
    finalize_releases,
    season_pack_release_sort_key,
    serialize_evaluated_release,
)
from app.siftarr.services.request_service import (
    ensure_tvdb_id,
    load_request_or_404,
    validate_tv_request,
)
from app.siftarr.services.rule_engine import RuleEngine
from app.siftarr.services.tv_details_service import (
    compute_sync_metadata,
    count_request_episode_states,
    count_season_episode_states,
    load_tv_seasons_with_episodes,
)
from app.siftarr.services.type_utils import coerce_int_list

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/requests", tags=["dashboard-api"])

SerializedObject = Mapping[str, object]


def _serialize_target_scope(
    *,
    media_type: MediaType,
    title: str,
    season_number: int | None = None,
    episode_number: int | None = None,
    season_coverage: str | None = None,
) -> dict[str, object]:
    """Serialize lightweight targeting metadata for releases and staged torrents."""
    if media_type != MediaType.TV:
        return {"type": "request"}

    coverage = parse_stored_release_coverage(season_coverage, season_number, episode_number)
    scoped_season_number = coverage.season_number
    scoped_episode_number = coverage.episode_number

    if (
        scoped_season_number is not None
        and scoped_episode_number is not None
        and is_exact_single_episode_release(title, scoped_season_number, scoped_episode_number)
    ):
        return {
            "type": "single_episode",
            "season_number": scoped_season_number,
            "episode_number": scoped_episode_number,
        }

    return {"type": "broad"}


def _as_serialized_object(value: object) -> SerializedObject | None:
    """Return mapping values with object payloads for typed key access."""
    if not isinstance(value, Mapping):
        return None
    return cast(SerializedObject, value)


def _release_matches_active_stage(
    release: SerializedObject,
    active_stage: SerializedObject,
    *,
    media_type: MediaType,
) -> bool:
    """Return True when a serialized release matches an active staged torrent."""
    if media_type != MediaType.TV:
        return release.get("title") == active_stage.get("title")

    release_scope = _as_serialized_object(release.get("target_scope"))
    active_scope = _as_serialized_object(active_stage.get("target_scope"))
    if (
        release_scope is not None
        and active_scope is not None
        and release_scope.get("type") == active_scope.get("type") == "single_episode"
    ):
        return release_scope.get("season_number") == active_scope.get(
            "season_number"
        ) and release_scope.get("episode_number") == active_scope.get("episode_number")

    return release.get("title") == active_stage.get("title")


@router.get("/{request_id}/details")
async def request_details(
    request_id: int,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    from app.siftarr.models.release import Release

    request = await load_request_or_404(db, request_id)

    effective_settings = get_settings()
    overseerr_service = OverseerrService(settings=effective_settings)
    details: dict[str, object] = {
        "request": {
            "id": request.id,
            "title": request.title,
            "status": request.status.value,
            "media_type": request.media_type.value,
        }
    }

    try:
        if request.overseerr_request_id:
            ov_task = asyncio.create_task(
                overseerr_service.get_request(request.overseerr_request_id)
            )
            media_details_task = None
            if request.media_type.value == "movie" and request.tmdb_id:
                media_details_task = asyncio.create_task(
                    overseerr_service.get_media_details("movie", request.tmdb_id)
                )
            elif request.media_type.value == "tv" and request.tmdb_id:
                media_details_task = asyncio.create_task(
                    overseerr_service.get_media_details("tv", request.tmdb_id)
                )

            ov = await ov_task
            media: dict[str, object] = {}
            request_status = "unknown"
            if ov:
                media = ov.get("media") or {}
                request_status = overseerr_service.normalize_media_status(media.get("status"))

            media_details = await media_details_task if media_details_task else None

            merged_media = {**media, **(media_details or {})}
            poster = build_poster_url(
                merged_media.get("posterPath") or merged_media.get("poster"),
            )

            details["overseerr"] = {
                "overview": merged_media.get("overview") or merged_media.get("summary") or "",
                "poster": poster,
                "status": request_status,
                "url": build_overseerr_media_url(
                    effective_settings.overseerr_url,
                    request.media_type.value,
                    request.tmdb_id,
                ),
            }
    finally:
        await overseerr_service.close()

    release_result = await db.execute(
        select(Release)
        .where(Release.request_id == request_id)
        .order_by(
            Release.score.desc(),
            Release.size.asc(),
            Release.seeders.desc(),
            Release.created_at.desc(),
        )
    )
    releases = list(release_result.scalars().all())
    rules = await db.execute(select(Rule))
    rule_list = list(rules.scalars().all())
    engine = RuleEngine.from_db_rules(rules=rule_list, media_type=request.media_type.value)

    matched = []
    for release in releases:
        evaluation = engine.evaluate(build_prowlarr_release(release))
        coverage = None
        if request.media_type == MediaType.TV:
            coverage = parse_stored_release_coverage(
                release.season_coverage,
                release.season_number,
                release.episode_number,
            )

        payload = serialize_evaluated_release(release, evaluation, coverage=coverage)
        payload.update(
            {
                "score": release.score,
                "passed": release.passed_rules,
                "rejection_reason": evaluation.rejection_reason,
                "season_number": release.season_number,
                "episode_number": release.episode_number,
                "matches": [
                    {
                        "rule_name": m.rule_name,
                        "matched": m.matched,
                        "score_delta": m.score_delta,
                    }
                    for m in evaluation.matches
                ],
                "target_scope": _serialize_target_scope(
                    media_type=request.media_type,
                    title=release.title,
                    season_number=release.season_number,
                    episode_number=release.episode_number,
                    season_coverage=release.season_coverage,
                ),
            }
        )
        matched.append(payload)

    matched = finalize_releases(matched)

    active_staged_torrents: list[StagedTorrent] = []
    if is_active_staging_workflow_status(request.status):
        active_staged_result = await db.execute(
            select(StagedTorrent)
            .where(
                StagedTorrent.request_id == request_id,
                StagedTorrent.status.in_(["staged", "approved"]),
            )
            .order_by(StagedTorrent.updated_at.desc(), StagedTorrent.created_at.desc())
        )
        active_staged_torrents = list(active_staged_result.scalars().all())

    active_staged_payloads = [
        {
            "id": active_staged_torrent.id,
            "title": active_staged_torrent.title,
            "status": active_staged_torrent.status,
            "selection_source": active_staged_torrent.selection_source,
            "target_scope": _serialize_target_scope(
                media_type=request.media_type,
                title=active_staged_torrent.title,
                season_number=parse_release_coverage(active_staged_torrent.title).season_number,
                episode_number=parse_release_coverage(active_staged_torrent.title).episode_number,
            ),
        }
        for active_staged_torrent in active_staged_torrents
    ]
    active_staged_payload = active_staged_payloads[0] if active_staged_payloads else None

    for release in matched:
        matching_active_stage = next(
            (
                active_stage
                for active_stage in active_staged_payloads
                if _release_matches_active_stage(
                    release,
                    active_stage,
                    media_type=request.media_type,
                )
            ),
            None,
        )
        release["is_active_selection"] = matching_active_stage is not None
        release["active_selection_status"] = (
            matching_active_stage.get("status") if matching_active_stage else None
        )
        release["active_selection_source"] = (
            matching_active_stage.get("selection_source") if matching_active_stage else None
        )
        release["active_staged_torrent"] = matching_active_stage

    details["releases"] = matched
    details["active_staged_torrent"] = active_staged_payload
    details["active_staged_torrents"] = active_staged_payloads

    if request.media_type == MediaType.TV:
        seasons, episodes = await load_tv_seasons_with_episodes(db, request_id)

        episodes_by_season: dict[int, list[Any]] = {}
        for episode in episodes:
            episodes_by_season.setdefault(episode.season_id, []).append(episode)

        sync_state = compute_sync_metadata(
            seasons,
            episodes_by_season,
            request_id,
            background_tasks,
        )

        seasons_data = []
        known_season_numbers: list[int] = []
        for season in seasons:
            known_season_numbers.append(season.season_number)
            season_episodes = episodes_by_season.get(season.id, [])
            available_count = sum(
                1 for ep in season_episodes if ep.status == RequestStatus.COMPLETED
            )
            state_counts = count_season_episode_states(season_episodes)
            season_data = {
                "id": season.id,
                "season_number": season.season_number,
                "status": season.status.value,
                "available_count": available_count,
                "total_count": len(season_episodes),
                "pending_count": state_counts["pending"],
                "unreleased_count": state_counts["unreleased"],
                "episodes": [
                    {
                        "id": ep.id,
                        "episode_number": ep.episode_number,
                        "title": ep.title,
                        "air_date": ep.air_date.isoformat() if ep.air_date else None,
                        "status": ep.status.value,
                    }
                    for ep in season_episodes
                ],
            }
            seasons_data.append(season_data)

        known_total_seasons = len(known_season_numbers)
        for release in matched:
            if "covered_seasons" not in release and not release.get("is_complete_series"):
                continue

            release["known_total_seasons"] = known_total_seasons
            covered_seasons = coerce_int_list(release.get("covered_seasons"))
            release["covers_all_known_seasons"] = bool(
                known_total_seasons
                and (
                    release.get("is_complete_series") or len(covered_seasons) >= known_total_seasons
                )
            )
            apply_release_size_per_season_metadata(release)

        releases_by_season: dict[int, list[dict[str, object]]] = {}
        releases_by_episode: dict[tuple[int, int], list[dict[str, object]]] = {}
        for r in matched:
            sn = r.get("season_number")
            en = r.get("episode_number")
            covered_seasons = coerce_int_list(r.get("covered_seasons"))
            if r.get("covers_all_known_seasons"):
                covered_seasons = known_season_numbers
            if isinstance(en, int) and isinstance(sn, int):
                key = (sn, en)
                if key not in releases_by_episode:
                    releases_by_episode[key] = []
                releases_by_episode[key].append(r)
            elif covered_seasons:
                for covered_season in covered_seasons:
                    if covered_season not in releases_by_season:
                        releases_by_season[covered_season] = []
                    releases_by_season[covered_season].append(r)
            elif isinstance(sn, int):
                if sn not in releases_by_season:
                    releases_by_season[sn] = []
                releases_by_season[sn].append(r)

        details["tv_info"] = {
            "seasons": seasons_data,
            "releases_by_season": {str(k): v for k, v in releases_by_season.items()},
            "releases_by_episode": {f"{k[0]}-{k[1]}": v for k, v in releases_by_episode.items()},
            "sync_state": sync_state,
            "aggregate_counts": count_request_episode_states(seasons_data),
        }

    # Timeline: activity log entries for this request
    activity_service = ActivityLogService(db)
    timeline_entries = await activity_service.get_timeline(request_id, limit=200)
    # Reverse to chronological order (oldest first) since get_timeline returns newest first
    timeline_entries.reverse()
    details["timeline"] = [
        {
            "id": entry.id,
            "event_type": entry.event_type,
            "details": json.loads(entry.details) if entry.details else None,
            "created_at": entry.created_at.isoformat() if entry.created_at else None,
        }
        for entry in timeline_entries
    ]

    return JSONResponse(details, background=background_tasks)


@router.post("/{request_id}/search")
async def search_request_releases(
    request_id: int,
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Trigger a search for a movie request and return updated releases as JSON."""
    from app.siftarr.models.release import Release

    request = await load_request_or_404(db, request_id)

    if request.media_type != MediaType.MOVIE:
        return JSONResponse(
            {"error": "Search endpoint is only available for movie requests"},
            status_code=400,
        )

    await _process_request_search(request, db)

    release_result = await db.execute(
        select(Release)
        .where(Release.request_id == request_id)
        .order_by(
            Release.score.desc(),
            Release.size.asc(),
            Release.seeders.desc(),
            Release.created_at.desc(),
        )
    )
    releases = list(release_result.scalars().all())
    rules = await db.execute(select(Rule))
    rule_list = list(rules.scalars().all())
    engine = RuleEngine.from_db_rules(rules=rule_list, media_type=request.media_type.value)

    matched = []
    for release in releases:
        evaluation = engine.evaluate(build_prowlarr_release(release))
        coverage = None
        if request.media_type == MediaType.TV:
            coverage = parse_stored_release_coverage(
                release.season_coverage,
                release.season_number,
                release.episode_number,
            )

        payload = serialize_evaluated_release(release, evaluation, coverage=coverage)
        payload.update(
            {
                "score": release.score,
                "passed": release.passed_rules,
                "rejection_reason": evaluation.rejection_reason,
                "season_number": release.season_number,
                "episode_number": release.episode_number,
                "matches": [
                    {
                        "rule_name": m.rule_name,
                        "matched": m.matched,
                        "score_delta": m.score_delta,
                    }
                    for m in evaluation.matches
                ],
            }
        )
        matched.append(payload)

    matched = finalize_releases(matched)

    return JSONResponse(
        {
            "releases": matched,
            "request": {
                "id": request.id,
                "title": request.title,
                "status": request.status.value,
                "media_type": request.media_type.value,
            },
        }
    )


@router.get("/{request_id}/seasons")
async def get_request_seasons(
    request_id: int,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Get seasons and episodes for a TV request."""
    request = await load_request_or_404(db, request_id)

    if request.media_type != MediaType.TV:
        return JSONResponse({"seasons": [], "message": "Request is not a TV show"})

    seasons, episodes = await load_tv_seasons_with_episodes(db, request_id)
    episodes_by_season: dict[int, list[Any]] = {}
    for episode in episodes:
        episodes_by_season.setdefault(episode.season_id, []).append(episode)
    sync_state = compute_sync_metadata(
        seasons,
        episodes_by_season,
        request_id,
        background_tasks,
    )

    seasons_data = []
    for season in seasons:
        season_episodes = episodes_by_season.get(season.id, [])

        season_data = {
            "id": season.id,
            "season_number": season.season_number,
            "status": season.status.value,
            "synced_at": season.synced_at.isoformat() if season.synced_at else None,
            "pending_count": count_season_episode_states(season_episodes)["pending"],
            "unreleased_count": count_season_episode_states(season_episodes)["unreleased"],
            "episodes": [
                {
                    "id": ep.id,
                    "episode_number": ep.episode_number,
                    "title": ep.title,
                    "air_date": ep.air_date.isoformat() if ep.air_date else None,
                    "status": ep.status.value,
                }
                for ep in season_episodes
            ],
        }
        seasons_data.append(season_data)

    return JSONResponse(
        {"seasons": seasons_data, "sync_state": sync_state},
        background=background_tasks,
    )


@router.post("/{request_id}/seasons/{season_number}/search")
async def search_season_packs(
    request_id: int,
    season_number: int,
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Search for season packs for a specific season."""
    request = await load_request_or_404(db, request_id)
    validate_tv_request(request)
    tvdb_id = ensure_tvdb_id(request)

    runtime_settings = get_settings()
    prowlarr = ProwlarrService(settings=runtime_settings)

    try:
        search_result = await prowlarr.search_by_tvdbid(
            tvdbid=tvdb_id,
            title=request.title,
            season=season_number,
            year=request.year,
        )

        if search_result.error:
            return JSONResponse({"error": search_result.error, "releases": []})

        rules_result = await db.execute(select(Rule))
        rules = list(rules_result.scalars().all())
        engine = RuleEngine.from_db_rules(rules=rules, media_type="tv")

        releases = []
        for release in search_result.releases:
            coverage = parse_release_coverage(release.title)
            if coverage.episode_number is not None:
                continue
            if coverage.is_complete_series:
                continue
            if coverage.season_numbers != (season_number,):
                continue
            evaluation = engine.evaluate(release)
            releases.append(serialize_evaluated_release(release, evaluation, coverage=coverage))

        return JSONResponse(
            {"releases": finalize_releases(releases, sort_key=season_pack_release_sort_key)}
        )
    finally:
        await prowlarr.close()


@router.post("/{request_id}/seasons/search-all")
async def search_all_season_packs(
    request_id: int,
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Search broadly for TV season packs without downloading anything."""
    from app.siftarr.models.season import Season

    request = await load_request_or_404(db, request_id)
    validate_tv_request(request)
    tvdb_id = ensure_tvdb_id(request)

    seasons_result = await db.execute(
        select(Season).where(Season.request_id == request_id).order_by(Season.season_number)
    )
    seasons = list(seasons_result.scalars().all())
    known_total_seasons = len(seasons) or None

    runtime_settings = get_settings()
    prowlarr = ProwlarrService(settings=runtime_settings)

    try:
        search_result = await prowlarr.search_by_tvdbid(
            tvdbid=tvdb_id,
            title=request.title,
            year=request.year,
        )

        if search_result.error:
            return JSONResponse({"error": search_result.error, "releases": []})

        rules_result = await db.execute(select(Rule))
        rules = list(rules_result.scalars().all())
        engine = RuleEngine.from_db_rules(rules=rules, media_type="tv")

        releases = []
        for release in search_result.releases:
            coverage = parse_release_coverage(release.title)
            if coverage.episode_number is not None:
                continue
            if not coverage.is_complete_series and len(coverage.season_numbers) <= 1:
                continue

            evaluation = engine.evaluate(release)
            releases.append(
                serialize_evaluated_release(
                    release,
                    evaluation,
                    coverage=coverage,
                    known_total_seasons=known_total_seasons,
                )
            )

        return JSONResponse(
            {
                "releases": finalize_releases(releases, sort_key=season_pack_release_sort_key),
                "known_total_seasons": known_total_seasons,
            }
        )
    finally:
        await prowlarr.close()


@router.post("/{request_id}/seasons/{season_number}/episodes/{episode_number}/search")
async def search_episode(
    request_id: int,
    season_number: int,
    episode_number: int,
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Search for a specific episode."""
    request = await load_request_or_404(db, request_id)
    validate_tv_request(request)
    tvdb_id = ensure_tvdb_id(request)

    runtime_settings = get_settings()
    prowlarr = ProwlarrService(settings=runtime_settings)

    try:
        search_result = await prowlarr.search_by_tvdbid(
            tvdbid=tvdb_id,
            title=request.title,
            season=season_number,
            episode=episode_number,
            year=request.year,
        )

        if search_result.error:
            return JSONResponse({"error": search_result.error, "releases": []})

        rules_result = await db.execute(select(Rule))
        rules = list(rules_result.scalars().all())
        engine = RuleEngine.from_db_rules(rules=rules, media_type="tv")

        releases = []
        for release in search_result.releases:
            coverage = parse_release_coverage(release.title)
            if coverage.is_complete_series:
                continue
            if coverage.season_numbers != (season_number,):
                continue
            if coverage.episode_number != episode_number:
                continue
            if not is_exact_single_episode_release(release.title, season_number, episode_number):
                continue
            evaluation = engine.evaluate(release)
            releases.append(serialize_evaluated_release(release, evaluation))

        return JSONResponse({"releases": finalize_releases(releases)})
    finally:
        await prowlarr.close()


@router.post("/{request_id}/refresh-plex")
async def refresh_plex(
    request_id: int,
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Run the simplified TV sync flow for one request."""
    from app.siftarr.services.episode_sync_service import EpisodeSyncService

    request = await load_request_or_404(db, request_id)

    if request.media_type != MediaType.TV:
        return JSONResponse({"error": "Request is not a TV show"})

    effective_settings = get_settings()
    plex_service = PlexService(settings=effective_settings)

    try:
        episode_sync = EpisodeSyncService(db, plex=plex_service)
        await episode_sync.sync_request(request_id)
        return JSONResponse({"status": "success", "message": "Plex sync completed"})
    except Exception:
        logger.exception("Plex refresh failed for request_id=%s", request_id)
        return JSONResponse({"status": "error", "message": "Plex sync failed"}, status_code=500)
    finally:
        await plex_service.close()
