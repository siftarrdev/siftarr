"""Dashboard data transfer objects and response serializers.

DTOs and serializers are kept here so router handlers and new sub-services
can share a single import without circular dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.siftarr.services.release_serializers import (
    season_pack_release_sort_key,  # noqa: F401
)


@dataclass(slots=True)
class DashboardRequestSummary:
    id: int
    title: str
    status: str
    media_type: str


@dataclass(slots=True)
class DashboardOverseerrDetails:
    overview: str
    poster: str | None
    status: str
    url: str | None
    release_date: str | None = None


@dataclass(slots=True)
class DashboardTVDetails:
    seasons: list[dict[str, object]]
    releases_by_season: dict[str, list[dict[str, object]]]
    releases_by_episode: dict[str, list[dict[str, object]]]
    sync_state: dict[str, object]
    aggregate_counts: dict[str, int]


@dataclass(slots=True)
class DashboardTimelineEntry:
    id: int
    event_type: str
    details: object | None
    created_at: str | None


@dataclass(slots=True)
class RequestDetailsData:
    request: DashboardRequestSummary
    releases: list[dict[str, object]]
    total_releases: int = 0
    active_staged_torrent: dict[str, object] | None = None
    active_staged_torrents: list[dict[str, object]] | None = None
    overseerr: DashboardOverseerrDetails | None = None
    tv_info: DashboardTVDetails | None = None
    timeline: list[DashboardTimelineEntry] | None = None


@dataclass(slots=True)
class RequestSearchData:
    request: DashboardRequestSummary
    releases: list[dict[str, object]]


@dataclass(slots=True)
class TVSearchData:
    releases: list[dict[str, object]]
    known_total_seasons: int | None = None
    scope: dict[str, object] | None = None
    error: str | None = None


def serialize_request_details_response(data: RequestDetailsData) -> dict[str, object]:
    """Convert request-details service DTOs into JSON-ready payloads."""
    payload: dict[str, object] = {
        "request": {
            "id": data.request.id,
            "title": data.request.title,
            "status": data.request.status,
            "media_type": data.request.media_type,
        },
        "releases": data.releases,
        "total_releases": data.total_releases,
        "active_staged_torrent": data.active_staged_torrent,
        "active_staged_torrents": data.active_staged_torrents,
        "timeline": [
            {
                "id": entry.id,
                "event_type": entry.event_type,
                "details": entry.details,
                "created_at": entry.created_at,
            }
            for entry in (data.timeline or [])
        ],
    }
    if data.overseerr is not None:
        payload["overseerr"] = {
            "overview": data.overseerr.overview,
            "poster": data.overseerr.poster,
            "status": data.overseerr.status,
            "url": data.overseerr.url,
            "release_date": data.overseerr.release_date,
        }
    if data.tv_info is not None:
        payload["tv_info"] = {
            "seasons": data.tv_info.seasons,
            "releases_by_season": data.tv_info.releases_by_season,
            "releases_by_episode": data.tv_info.releases_by_episode,
            "sync_state": data.tv_info.sync_state,
            "aggregate_counts": data.tv_info.aggregate_counts,
        }
    return payload


def serialize_request_search_response(data: RequestSearchData) -> dict[str, object]:
    """Convert movie-search service DTOs into JSON-ready payloads."""
    return {
        "releases": data.releases,
        "request": {
            "id": data.request.id,
            "title": data.request.title,
            "status": data.request.status,
            "media_type": data.request.media_type,
        },
    }


def serialize_tv_search_response(data: TVSearchData) -> dict[str, object]:
    """Convert TV-search service DTOs into JSON-ready payloads."""
    payload: dict[str, object] = {"releases": data.releases}
    if data.scope is not None:
        payload["scope"] = data.scope
    if data.error is not None:
        payload["error"] = data.error
    if data.known_total_seasons is not None:
        payload["known_total_seasons"] = data.known_total_seasons
    return payload
