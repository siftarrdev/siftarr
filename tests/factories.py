"""Persisted model factories for tests that use the shared ``db`` fixture."""

from datetime import date
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.siftarr.models import Episode, Release, Request, Rule, Season, StagedTorrent
from app.siftarr.models.request import MediaType, RequestStatus
from app.siftarr.models.rule import RuleType


async def _save[T](db: AsyncSession, obj: T) -> T:
    db.add(obj)
    await db.flush()
    return obj


async def make_request(db: AsyncSession, **overrides: Any) -> Request:
    media_type = overrides.pop("media_type", MediaType.MOVIE)
    defaults: dict[str, Any] = {
        "external_id": "1",
        "media_type": media_type,
        "title": "Example Movie" if media_type == MediaType.MOVIE else "Example Show",
        "year": 2025,
        "status": RequestStatus.PENDING,
    }
    if media_type == MediaType.MOVIE:
        defaults["tmdb_id"] = 1
    else:
        defaults["tvdb_id"] = 1
    return await _save(db, Request(**(defaults | overrides)))


async def make_season(
    db: AsyncSession,
    request: Request,
    season_number: int = 1,
    *,
    episodes: int = 0,
    status: RequestStatus = RequestStatus.PENDING,
    episode_status: RequestStatus | None = None,
    air_date: date | None = date(2020, 1, 1),
) -> Season:
    """Create a season, optionally with ``episodes`` numbered from 1."""
    season = await _save(
        db, Season(request_id=request.id, season_number=season_number, status=status)
    )
    for number in range(1, episodes + 1):
        await make_episode(db, season, number, status=episode_status or status, air_date=air_date)
    return season


async def make_episode(
    db: AsyncSession, season: Season, episode_number: int = 1, **overrides: Any
) -> Episode:
    defaults: dict[str, Any] = {
        "season_id": season.id,
        "episode_number": episode_number,
        "air_date": date(2020, 1, 1),
        "status": RequestStatus.PENDING,
    }
    return await _save(db, Episode(**(defaults | overrides)))


async def make_release(db: AsyncSession, request: Request, **overrides: Any) -> Release:
    defaults: dict[str, Any] = {
        "request_id": request.id,
        "title": f"{request.title} {request.year or ''} 1080p WEB-DL x264-GRP".replace("  ", " "),
        "size": 2 * 1024**3,
        "seeders": 50,
        "leechers": 5,
        "download_url": "https://indexer.example/download/1",
        "indexer": "Indexer",
        "score": 100,
        "passed_rules": True,
    }
    return await _save(db, Release(**(defaults | overrides)))


async def make_staged_torrent(
    db: AsyncSession, request: Request | None = None, **overrides: Any
) -> StagedTorrent:
    defaults: dict[str, Any] = {
        "request_id": request.id if request else None,
        "torrent_path": "/data/staging/example.torrent",
        "json_path": "/data/staging/example.json",
        "original_filename": "example.torrent",
        "title": "Example Movie 2025 1080p WEB-DL x264-GRP",
        "size": 2 * 1024**3,
        "indexer": "Indexer",
        "score": 100,
        "selection_source": "rule",
        "status": "staged",
    }
    return await _save(db, StagedTorrent(**(defaults | overrides)))


async def make_rule(db: AsyncSession, **overrides: Any) -> Rule:
    defaults: dict[str, Any] = {
        "name": "Rule",
        "rule_type": RuleType.EXCLUSION,
        "media_scope": "both",
        "pattern": "cam",
        "score": 0,
        "priority": 0,
        "is_enabled": True,
    }
    return await _save(db, Rule(**(defaults | overrides)))
