"""Tests for settings router manual actions and Overseerr sync behavior."""

from datetime import date, timedelta
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.siftarr.models._base import Base
from app.siftarr.models.request import MediaType, Request, RequestStatus
from app.siftarr.routers import settings


@pytest_asyncio.fixture
async def session():
    """Provide an in-memory SQLite AsyncSession with schema created."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    async with session_maker() as db:
        yield db
    await engine.dispose()


def _sync_context(request: MagicMock | None = None) -> dict:
    """Build the minimal settings-page context needed by sync_overseerr."""
    return {
        "request": request or MagicMock(),
        "env": {
            "overseerr_url": "https://overseerr.test",
            "overseerr_api_key": "secret",
        },
        "staging_enabled": True,
        "pending_count": 0,
        "stats": {"total_requests": 0, "completed": 0, "pending": 0, "failed": 0},
    }


def _overseerr_request(*, request_id: int, media: dict, status: str = "approved") -> dict:
    """Build a minimal Overseerr request payload used by the sync route."""
    return {
        "id": request_id,
        "status": status,
        "media": media,
        "requestedBy": {
            "username": "alice",
            "email": "alice@example.com",
        },
    }


async def _get_request_by_external_id(db, external_id: str) -> Request | None:
    """Fetch a persisted request by external ID."""
    result = await db.execute(select(Request).where(Request.external_id == external_id))
    return result.scalar_one_or_none()


class TestSettingsRouterSyncOverseerr:
    """Regression coverage for sync_overseerr import-time status handling."""

    @pytest.mark.asyncio
    async def test_sync_overseerr_movie_import_marks_fresh_unreleased_as_unreleased(
        self, session, monkeypatch
    ):
        """Freshly imported unreleased movies should land in UNRELEASED after import evaluation."""
        future_date = (date.today() + timedelta(days=30)).isoformat()
        overseerr = MagicMock()
        overseerr.get_all_requests = AsyncMock(
            return_value=[
                _overseerr_request(
                    request_id=101,
                    media={
                        "tmdbId": 9001,
                        "mediaType": "movie",
                        "status": "processing",
                    },
                )
            ]
        )
        overseerr.get_media_details = AsyncMock(
            return_value={
                "status": "Post Production",
                "releaseDate": future_date,
                "releases": {"results": []},
            }
        )
        overseerr.get_request = AsyncMock(return_value=None)
        overseerr.get_season_details = AsyncMock(return_value=None)
        overseerr.normalize_request_status = MagicMock(side_effect=lambda value: value)
        overseerr.normalize_media_status = MagicMock(side_effect=lambda value: value)
        overseerr.close = AsyncMock()

        monkeypatch.setattr(
            settings, "_build_settings_page_context", AsyncMock(return_value=_sync_context())
        )
        monkeypatch.setattr(settings, "get_effective_settings", AsyncMock(return_value=MagicMock()))
        monkeypatch.setattr(settings, "OverseerrService", lambda **kwargs: overseerr)
        monkeypatch.setattr(
            settings,
            "extract_media_title_and_year",
            AsyncMock(return_value=("Future Movie", 2027)),
        )

        response = await settings.sync_overseerr(MagicMock(), db=session)
        imported = await _get_request_by_external_id(session, "9001")

        assert cast(dict, getattr(response, "context", None))["message_type"] == "success"
        assert imported is not None
        assert imported.media_type == MediaType.MOVIE
        assert imported.status == RequestStatus.UNRELEASED
        overseerr.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_sync_overseerr_tv_import_marks_fresh_unreleased_as_unreleased(
        self, session, monkeypatch
    ):
        """Freshly imported fully unaired TV requests should also land in UNRELEASED."""
        future_date = (date.today() + timedelta(days=45)).isoformat()
        overseerr = MagicMock()
        overseerr.get_all_requests = AsyncMock(
            return_value=[
                _overseerr_request(
                    request_id=202,
                    media={
                        "tmdbId": 7001,
                        "tvdbId": 8001,
                        "mediaType": "tv",
                        "status": "processing",
                        "requestedSeasons": [1],
                    },
                )
            ]
        )
        overseerr.get_media_details = AsyncMock(
            return_value={
                "status": "Planned",
                "firstAirDate": future_date,
                "seasons": [],
                "mediaInfo": {"seasons": []},
            }
        )
        overseerr.get_request = AsyncMock(return_value={"seasons": []})
        overseerr.get_season_details = AsyncMock(return_value=None)
        overseerr.normalize_request_status = MagicMock(side_effect=lambda value: value)
        overseerr.normalize_media_status = MagicMock(side_effect=lambda value: value)
        overseerr.close = AsyncMock()

        monkeypatch.setattr(
            settings, "_build_settings_page_context", AsyncMock(return_value=_sync_context())
        )
        monkeypatch.setattr(settings, "get_effective_settings", AsyncMock(return_value=MagicMock()))
        monkeypatch.setattr(settings, "OverseerrService", lambda **kwargs: overseerr)
        monkeypatch.setattr(
            settings,
            "extract_media_title_and_year",
            AsyncMock(return_value=("Future Show", 2027)),
        )

        response = await settings.sync_overseerr(MagicMock(), db=session)
        imported = await _get_request_by_external_id(session, "7001")

        assert cast(dict, getattr(response, "context", None))["message_type"] == "success"
        assert imported is not None
        assert imported.media_type == MediaType.TV
        assert imported.status == RequestStatus.UNRELEASED
        overseerr.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_sync_overseerr_released_import_stays_pending(self, session, monkeypatch):
        """Released imports should remain in the normal PENDING path after sync."""
        past_date = (date.today() - timedelta(days=30)).isoformat()
        overseerr = MagicMock()
        overseerr.get_all_requests = AsyncMock(
            return_value=[
                _overseerr_request(
                    request_id=303,
                    media={
                        "tmdbId": 9101,
                        "mediaType": "movie",
                        "status": "processing",
                    },
                )
            ]
        )
        overseerr.get_media_details = AsyncMock(
            return_value={
                "status": "Released",
                "releaseDate": past_date,
                "releases": {"results": []},
            }
        )
        overseerr.get_request = AsyncMock(return_value=None)
        overseerr.get_season_details = AsyncMock(return_value=None)
        overseerr.normalize_request_status = MagicMock(side_effect=lambda value: value)
        overseerr.normalize_media_status = MagicMock(side_effect=lambda value: value)
        overseerr.close = AsyncMock()

        monkeypatch.setattr(
            settings, "_build_settings_page_context", AsyncMock(return_value=_sync_context())
        )
        monkeypatch.setattr(settings, "get_effective_settings", AsyncMock(return_value=MagicMock()))
        monkeypatch.setattr(settings, "OverseerrService", lambda **kwargs: overseerr)
        monkeypatch.setattr(
            settings,
            "extract_media_title_and_year",
            AsyncMock(return_value=("Released Movie", 2024)),
        )

        await settings.sync_overseerr(MagicMock(), db=session)
        imported = await _get_request_by_external_id(session, "9101")

        assert imported is not None
        assert imported.status == RequestStatus.PENDING

    @pytest.mark.asyncio
    async def test_sync_overseerr_skips_duplicates_unchanged(self, session, monkeypatch):
        """Existing requests should still be skipped during Overseerr sync."""
        existing = Request(
            external_id="9201",
            media_type=MediaType.MOVIE,
            tmdb_id=9201,
            title="Existing Movie",
            status=RequestStatus.PENDING,
            overseerr_request_id=404,
        )
        session.add(existing)
        await session.commit()

        overseerr = MagicMock()
        overseerr.get_all_requests = AsyncMock(
            return_value=[
                _overseerr_request(
                    request_id=404,
                    media={
                        "tmdbId": 9201,
                        "mediaType": "movie",
                        "status": "processing",
                    },
                )
            ]
        )
        overseerr.get_media_details = AsyncMock()
        overseerr.get_request = AsyncMock(return_value=None)
        overseerr.get_season_details = AsyncMock(return_value=None)
        overseerr.normalize_request_status = MagicMock(side_effect=lambda value: value)
        overseerr.normalize_media_status = MagicMock(side_effect=lambda value: value)
        overseerr.close = AsyncMock()

        monkeypatch.setattr(
            settings, "_build_settings_page_context", AsyncMock(return_value=_sync_context())
        )
        monkeypatch.setattr(settings, "get_effective_settings", AsyncMock(return_value=MagicMock()))
        monkeypatch.setattr(settings, "OverseerrService", lambda **kwargs: overseerr)
        monkeypatch.setattr(settings, "extract_media_title_and_year", AsyncMock())

        response = await settings.sync_overseerr(MagicMock(), db=session)

        result = await session.execute(select(Request).where(Request.external_id == "9201"))
        rows = result.scalars().all()

        assert len(rows) == 1
        assert (
            cast(dict, getattr(response, "context", None))["message"]
            == "No new actionable requests to sync (1 already existed or were already available)"
        )
        overseerr.get_media_details.assert_not_awaited()
        overseerr.close.assert_awaited_once()


class TestSettingsRouter:
    """Focused tests for settings manual actions."""

    @pytest.mark.asyncio
    async def test_get_settings_page_includes_clear_cache_scope_copy(self, monkeypatch):
        """Settings page should describe the app-side cache-clearing scope and limits."""
        mock_db = AsyncMock()
        rule_service = MagicMock()
        rule_service.ensure_default_rules = AsyncMock()

        monkeypatch.setattr(settings, "RuleService", lambda db: rule_service)
        monkeypatch.setattr(
            settings,
            "_build_settings_page_context",
            AsyncMock(
                return_value={
                    "request": MagicMock(),
                    "env": {},
                    "staging_enabled": True,
                    "pending_count": 0,
                    "stats": {"total_requests": 0, "completed": 0, "pending": 0, "failed": 0},
                }
            ),
        )

        response = await settings.get_settings_page(MagicMock(), db=mock_db)
        body = cast(bytes, response.body).decode()

        assert "Clear App Search Cache" in body
        assert "releases table" in body
        assert "Overseerr status cache" in body
        assert "external/manual Prowlarr caching cannot be guaranteed" in body

    @pytest.mark.asyncio
    async def test_clear_cache_route_reports_success(self, monkeypatch):
        """Clear-cache action should report what was removed from app-side caches."""
        mock_db = AsyncMock()
        base_context = {
            "request": MagicMock(),
            "env": {},
            "staging_enabled": True,
            "pending_count": 0,
            "stats": {"total_requests": 0, "completed": 0, "pending": 0, "failed": 0},
        }

        monkeypatch.setattr(
            settings,
            "_build_settings_page_context",
            AsyncMock(return_value=base_context.copy()),
        )
        monkeypatch.setattr(
            settings,
            "clear_release_search_cache",
            AsyncMock(return_value={"deleted_releases": 4, "detached_episode_refs": 2}),
        )
        monkeypatch.setattr(settings, "clear_status_cache", MagicMock(return_value=3))

        response = await settings.clear_cache(MagicMock(), db=mock_db)
        context = cast(dict, getattr(response, "context", None))

        assert context["message_type"] == "success"
        assert "removed 4 stored release result(s)" in context["message"]
        assert "detached 2 episode link(s)" in context["message"]
        assert "cleared 3 Overseerr status cache entries" in context["message"]

    @pytest.mark.asyncio
    async def test_clear_cache_route_reports_failure_and_rolls_back(self, monkeypatch):
        """Clear-cache errors should be surfaced without leaving the transaction open."""
        mock_db = AsyncMock()
        base_context = {
            "request": MagicMock(),
            "env": {},
            "staging_enabled": True,
            "pending_count": 0,
            "stats": {"total_requests": 0, "completed": 0, "pending": 0, "failed": 0},
        }

        monkeypatch.setattr(
            settings,
            "_build_settings_page_context",
            AsyncMock(return_value=base_context.copy()),
        )
        monkeypatch.setattr(
            settings,
            "clear_release_search_cache",
            AsyncMock(side_effect=RuntimeError("boom")),
        )
        clear_status_cache = MagicMock()
        monkeypatch.setattr(settings, "clear_status_cache", clear_status_cache)

        response = await settings.clear_cache(MagicMock(), db=mock_db)
        context = cast(dict, getattr(response, "context", None))

        assert context["message_type"] == "error"
        assert context["message"] == "Failed to clear app search cache: boom"
        mock_db.rollback.assert_awaited_once()
        clear_status_cache.assert_not_called()

    @pytest.mark.asyncio
    async def test_settings_page_includes_reseed_default_snapshot_copy(self, monkeypatch):
        """Settings copy should describe reseeding the checked-in 12-rule snapshot."""
        mock_db = AsyncMock()
        rule_service = MagicMock()
        rule_service.ensure_default_rules = AsyncMock()

        monkeypatch.setattr(settings, "RuleService", lambda db: rule_service)
        monkeypatch.setattr(
            settings,
            "_build_settings_page_context",
            AsyncMock(
                return_value={
                    "request": MagicMock(),
                    "env": {},
                    "staging_enabled": True,
                    "pending_count": 0,
                    "stats": {"total_requests": 0, "completed": 0, "pending": 0, "failed": 0},
                }
            ),
        )

        response = await settings.get_settings_page(MagicMock(), db=mock_db)
        body = cast(bytes, response.body).decode()

        assert "checked-in 12-rule default snapshot" in body
