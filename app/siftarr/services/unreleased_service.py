"""Unreleased evaluator service.

Includes release-state detection for movies and TV shows.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import date, datetime
from typing import Literal, Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.siftarr.models.episode import Episode
from app.siftarr.models.request import MediaType, Request, RequestStatus
from app.siftarr.models.season import Season
from app.siftarr.services.episode_derive import derive_request_status_from_episodes
from app.siftarr.services.lifecycle_service import LifecycleService
from app.siftarr.services.overseerr_service import OverseerrService

_logger = logging.getLogger(__name__)

_RELEASE_TYPES_AVAILABLE = {3, 4, 5}
_TV_UNAIRED_STATUSES = {"Planned", "In Production", "Pilot"}
_AVAILABLE_EPISODE_STATUSES = {RequestStatus.COMPLETED}


class EpisodeLike(Protocol):
    air_date: date | None
    status: RequestStatus


class ReleaseCheckRequestLike(Protocol):
    media_type: MediaType
    tmdb_id: int | None


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        pass
    try:
        return datetime.fromisoformat(value).date()
    except ValueError:
        return None


def _is_unreleased_movie(
    details: dict | None,
    *,
    today: date | None = None,
) -> bool:
    if details is None:
        return False

    today = today or date.today()
    status = details.get("status")
    status_not_released = status != "Released"
    release_date = _parse_date(details.get("releaseDate"))
    release_date_missing_or_future = release_date is None or release_date > today

    has_past_avail_release = False
    releases_block = details.get("releases")
    if isinstance(releases_block, dict):
        results = releases_block.get("results")
        if isinstance(results, list):
            for country in results:
                if not isinstance(country, dict):
                    continue
                dates = country.get("release_dates")
                if not isinstance(dates, list):
                    continue
                for entry in dates:
                    if not isinstance(entry, dict):
                        continue
                    if entry.get("type") not in _RELEASE_TYPES_AVAILABLE:
                        continue
                    parsed = _parse_date(entry.get("release_date"))
                    if parsed is not None and parsed <= today:
                        has_past_avail_release = True
                        break
                if has_past_avail_release:
                    break

    return status_not_released and release_date_missing_or_future and not has_past_avail_release


def _is_unreleased_tv_request(
    tv_details: dict | None,
    local_episodes: Iterable[EpisodeLike],
    *,
    today: date | None = None,
    has_empty_seasons: bool = False,
) -> bool:
    if tv_details is None:
        return False

    today = today or date.today()
    episodes = list(local_episodes)
    next_episode = tv_details.get("nextEpisodeToAir")
    next_episode_air_date = None
    if isinstance(next_episode, dict):
        next_episode_air_date = _parse_date(
            next_episode.get("airDate") or next_episode.get("airDateUtc")
        )
    has_future_signal = has_empty_seasons or (
        next_episode_air_date is not None and next_episode_air_date > today
    )

    any_aired_locally = any(e.air_date is not None and e.air_date <= today for e in episodes)
    first_air = _parse_date(tv_details.get("firstAirDate"))
    first_air_missing_or_future = first_air is None or first_air > today
    series_status = tv_details.get("status")
    series_status_unaired = series_status in _TV_UNAIRED_STATUSES

    if (first_air_missing_or_future or series_status_unaired) and not any_aired_locally:
        return True

    if any_aired_locally:
        aired = [e for e in episodes if e.air_date is not None and e.air_date <= today]
        all_aired_downloaded = all(e.status in _AVAILABLE_EPISODE_STATUSES for e in aired)
        has_future_or_unknown = has_future_signal or any(
            e.air_date is not None and e.air_date > today for e in episodes
        )
        has_unreleased_no_date_placeholder = any(
            e.air_date is None and e.status == RequestStatus.UNRELEASED for e in episodes
        )
        return all_aired_downloaded and (
            has_future_or_unknown or has_unreleased_no_date_placeholder
        )

    return False


def is_unreleased(
    request: ReleaseCheckRequestLike,
    *,
    media_details: dict | None,
    local_episodes: Iterable[EpisodeLike] = (),
    today: date | None = None,
    has_empty_seasons: bool = False,
) -> bool:
    if request.tmdb_id is None:
        return False

    if request.media_type == MediaType.MOVIE:
        return _is_unreleased_movie(media_details, today=today)

    return _is_unreleased_tv_request(
        media_details,
        local_episodes,
        today=today,
        has_empty_seasons=has_empty_seasons,
    )


_REDIRECTABLE_STATUSES = {
    RequestStatus.PENDING,
    RequestStatus.SEARCHING,
    RequestStatus.COMPLETED,  # Allow ongoing TV series to be re-classified as unreleased
}


class UnreleasedEvaluator:
    def __init__(self, db: AsyncSession, overseerr: OverseerrService) -> None:
        self.db = db
        self.overseerr = overseerr
        self.lifecycle = LifecycleService(db)

    async def evaluate(
        self,
        request: Request,
        *,
        prefetched_media_details: dict | None = None,
        local_episodes: Iterable[EpisodeLike] | None = None,
    ) -> Literal["released", "unreleased"]:
        media_details = prefetched_media_details
        if request.tmdb_id is not None and media_details is None:
            media_type = "movie" if request.media_type == MediaType.MOVIE else "tv"
            media_details = await self.overseerr.get_media_details(media_type, request.tmdb_id)

        resolved_local_episodes = local_episodes
        if request.media_type == MediaType.TV and resolved_local_episodes is None:
            result = await self.db.execute(
                select(Episode)
                .join(Season, Season.id == Episode.season_id)
                .where(Season.request_id == request.id)
            )
            resolved_local_episodes = list(result.scalars().all())

        has_empty_seasons = False
        if request.media_type == MediaType.TV:
            seasons_result = await self.db.execute(
                select(Season).where(Season.request_id == request.id)
            )
            all_seasons = list(seasons_result.scalars().all())
            db_episodes: list[Episode] = [
                ep for ep in (resolved_local_episodes or []) if isinstance(ep, Episode)
            ]
            season_ids_with_episodes = {ep.season_id for ep in db_episodes}
            has_empty_seasons = any(s.id not in season_ids_with_episodes for s in all_seasons)

        return (
            "unreleased"
            if is_unreleased(
                request,
                media_details=media_details,
                local_episodes=resolved_local_episodes or (),
                has_empty_seasons=has_empty_seasons,
            )
            else "released"
        )

    async def apply_verdict(
        self,
        request: Request,
        verdict: Literal["released", "unreleased"],
    ) -> RequestStatus | None:
        current = request.status

        if request.media_type == MediaType.TV:
            return await self._apply_verdict_tv(request, verdict)

        if verdict == "unreleased" and current in _REDIRECTABLE_STATUSES:
            _logger.info(
                "UnreleasedEvaluator: reclassifying request_id=%s title=%s from %s to unreleased",
                request.id,
                request.title,
                current.value,
            )
            updated = await self.lifecycle.transition(
                request.id,
                RequestStatus.UNRELEASED,
                reason="reclassified to unreleased after release-status recheck",
            )
            if updated is not None:
                return RequestStatus.UNRELEASED
            return None

        if verdict == "released" and current == RequestStatus.UNRELEASED:
            updated = await self.lifecycle.transition(request.id, RequestStatus.PENDING)
            if updated is not None:
                return RequestStatus.PENDING
            return None

        return None

    async def _apply_verdict_tv(
        self,
        request: Request,
        verdict: Literal["released", "unreleased"],
    ) -> RequestStatus | None:
        """Apply verdict at the episode level for TV requests.

        After mutating episode statuses, always derives the cached
        request-level status from episodes to keep it in sync.
        """
        # Load all episodes for this request
        result = await self.db.execute(
            select(Episode)
            .join(Season, Season.id == Episode.season_id)
            .where(Season.request_id == request.id)
        )
        all_episodes = list(result.scalars().all())

        if verdict == "unreleased":
            # Redirectable for TV: any episode is PENDING, SEARCHING, or COMPLETED
            has_redirectable = any(ep.status in _REDIRECTABLE_STATUSES for ep in all_episodes)
            if not has_redirectable:
                # Still sync cached status even when not redirectable
                request.status = derive_request_status_from_episodes(all_episodes)
                await self.db.commit()
                return request.status

            _logger.info(
                "UnreleasedEvaluator(TV): reclassifying request_id=%s title=%s "
                "setting PENDING episodes to UNRELEASED",
                request.id,
                request.title,
            )
            for ep in all_episodes:
                if ep.status == RequestStatus.PENDING:
                    ep.status = RequestStatus.UNRELEASED
            if all_episodes:
                await self.db.flush()
            # Derive cached request status from episodes
            request.status = derive_request_status_from_episodes(all_episodes)
            await self.db.commit()
            return request.status

        if verdict == "released":
            _logger.info(
                "UnreleasedEvaluator(TV): setting UNRELEASED episodes to PENDING "
                "for request_id=%s title=%s",
                request.id,
                request.title,
            )
            for ep in all_episodes:
                if ep.status == RequestStatus.UNRELEASED:
                    ep.status = RequestStatus.PENDING
            if all_episodes:
                await self.db.flush()
            # Derive cached request status from episodes
            request.status = derive_request_status_from_episodes(all_episodes)
            await self.db.commit()
            return request.status

        return None

    async def evaluate_and_apply(
        self,
        request: Request,
        *,
        prefetched_media_details: dict | None = None,
        local_episodes: Iterable[EpisodeLike] | None = None,
    ) -> RequestStatus | None:
        verdict = await self.evaluate(
            request,
            prefetched_media_details=prefetched_media_details,
            local_episodes=local_episodes,
        )
        return await self.apply_verdict(request, verdict)


async def evaluate_imported_request(
    db: AsyncSession,
    overseerr: OverseerrService,
    request: Request,
    *,
    logger: logging.Logger | None = None,
    prefetched_media_details: dict | None = None,
    local_episodes: Iterable[EpisodeLike] | None = None,
) -> RequestStatus | None:
    active_logger = logger or _logger
    try:
        await db.refresh(request)
        new_status = await UnreleasedEvaluator(db, overseerr).evaluate_and_apply(
            request,
            prefetched_media_details=prefetched_media_details,
            local_episodes=local_episodes,
        )
        await db.refresh(request)
        return new_status
    except Exception:
        active_logger.exception(
            "Unreleased evaluation failed for imported request_id=%s", request.id
        )
        return None
