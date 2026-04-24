"""Tests for LifecycleService."""

from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call

import pytest

from app.siftarr.models.request import Request, RequestStatus
from app.siftarr.services.lifecycle_service import (
    LifecycleService,
    is_unreleased,
)


class TestLifecycleService:
    """Test cases for LifecycleService."""

    @pytest.fixture
    def mock_db(self):
        """Create a mock database session."""
        db = AsyncMock()
        db.add = MagicMock()
        return db

    @pytest.fixture
    def service(self, mock_db):
        """Create a LifecycleService instance."""
        return LifecycleService(mock_db)

    @pytest.mark.asyncio
    async def test_get_active_requests_includes_searching(self, mock_db, service):
        """Searching requests should be returned as active."""
        mock_requests = [MagicMock(spec=Request, id=1, status=RequestStatus.SEARCHING)]
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = mock_requests
        mock_db.execute.return_value = mock_result

        result = await service.get_active_requests()

        assert len(result) == 1
        assert result[0].status == RequestStatus.SEARCHING

    @pytest.mark.asyncio
    async def test_transition_success(self, mock_db, service):
        """Test successful status transition."""
        mock_request = MagicMock(spec=Request)
        mock_request.status = RequestStatus.PENDING
        mock_request.id = 1

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_request
        mock_db.execute.return_value = mock_result

        result = await service.transition(1, RequestStatus.SEARCHING)

        assert result == mock_request
        assert result.status == RequestStatus.SEARCHING
        assert mock_db.commit.call_count == 2
        assert mock_db.commit.call_args_list == [call(), call()]
        mock_db.refresh.assert_called_once()

    @pytest.mark.asyncio
    async def test_transition_request_not_found(self, mock_db, service):
        """Test transition when request doesn't exist."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result

        result = await service.transition(999, RequestStatus.SEARCHING)

        assert result is None
        mock_db.commit.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_request_status(self, mock_db, service):
        """Test getting request status."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = RequestStatus.SEARCHING
        mock_db.execute.return_value = mock_result

        result = await service.get_request_status(1)

        assert result == RequestStatus.SEARCHING

    @pytest.mark.asyncio
    async def test_get_active_requests(self, mock_db, service):
        """Test getting active requests."""
        mock_requests = [
            MagicMock(spec=Request, id=1, status=RequestStatus.SEARCHING),
            MagicMock(spec=Request, id=2, status=RequestStatus.PENDING),
        ]
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = mock_requests
        mock_db.execute.return_value = mock_result

        result = await service.get_active_requests()

        assert len(result) == 2
        mock_db.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_requests_by_status(self, mock_db, service):
        """Test getting requests by specific status."""
        mock_requests = [
            MagicMock(spec=Request, id=1, status=RequestStatus.PENDING),
        ]
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = mock_requests
        mock_db.execute.return_value = mock_result

        result = await service.get_requests_by_status(RequestStatus.PENDING)

        assert len(result) == 1
        assert result[0].status == RequestStatus.PENDING

    @pytest.mark.asyncio
    async def test_get_requests_stats(self, mock_db, service):
        """Test getting request statistics using SQL aggregates."""
        mock_result = MagicMock()
        mock_result.all.return_value = [
            (RequestStatus.COMPLETED, 2),
            (RequestStatus.PENDING, 1),
            (RequestStatus.FAILED, 1),
        ]
        mock_db.execute.return_value = mock_result

        result = await service.get_requests_stats()

        assert result["total"] == 4
        assert result["by_status"]["completed"] == 2
        assert result["by_status"]["pending"] == 1
        assert result["by_status"]["failed"] == 1

    @pytest.mark.asyncio
    async def test_update_request_metadata(self, mock_db, service):
        """Test updating request metadata."""
        mock_request = MagicMock(spec=Request)
        mock_request.id = 1
        mock_request.title = "Old Title"
        mock_request.year = 2020

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_request
        mock_db.execute.return_value = mock_result

        result = await service.update_request_metadata(1, title="New Title", year=2024)

        assert result.title == "New Title"
        assert result.year == 2024
        mock_db.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_update_request_metadata_not_found(self, mock_db, service):
        """Test updating metadata for non-existent request."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result

        result = await service.update_request_metadata(999, title="New Title")

        assert result is None


TODAY = date(2026, 4, 17)


def test_is_unreleased_movie_future_release():
    request = SimpleNamespace(media_type="movie", tmdb_id=123)
    details = {
        "status": "In Production",
        "releaseDate": "2026-08-01",
        "releases": {"results": []},
    }
    assert is_unreleased(request, media_details=details, today=TODAY) is True


def test_is_unreleased_tv_all_aired_downloaded_with_future_remaining():
    request = SimpleNamespace(media_type="tv", tmdb_id=456)
    details = {
        "firstAirDate": "2025-01-01",
        "status": "Returning Series",
    }
    episodes = [
        SimpleNamespace(air_date=date(2026, 4, 1), status=RequestStatus.COMPLETED),
        SimpleNamespace(air_date=date(2026, 4, 8), status=RequestStatus.COMPLETED),
        SimpleNamespace(air_date=date(2026, 5, 1), status=RequestStatus.UNRELEASED),
    ]
    assert (
        is_unreleased(request, media_details=details, local_episodes=episodes, today=TODAY) is True
    )


def test_is_unreleased_tv_actively_airing_completed_so_far_with_future_episode():
    """Actively airing seasons are unreleased after all aired episodes are complete."""
    request = SimpleNamespace(media_type="tv", tmdb_id=456)
    details = {
        "firstAirDate": "2025-01-01",
        "status": "Returning Series",
    }
    season_episodes = [
        SimpleNamespace(air_date=date(2026, 4, 3), status=RequestStatus.COMPLETED),
        SimpleNamespace(air_date=date(2026, 4, 10), status=RequestStatus.COMPLETED),
        SimpleNamespace(air_date=date(2026, 4, 24), status=RequestStatus.UNRELEASED),
    ]

    assert (
        is_unreleased(
            request,
            media_details=details,
            local_episodes=season_episodes,
            today=TODAY,
        )
        is True
    )


def test_is_unreleased_tv_future_season_no_air_date_placeholder():
    """A no-air-date placeholder marks a future season once prior aired episodes are complete."""
    request = SimpleNamespace(media_type="tv", tmdb_id=456)
    details = {
        "firstAirDate": "2025-01-01",
        "status": "Returning Series",
    }
    episodes = [
        SimpleNamespace(air_date=date(2026, 3, 20), status=RequestStatus.COMPLETED),
        SimpleNamespace(air_date=date(2026, 3, 27), status=RequestStatus.COMPLETED),
        SimpleNamespace(air_date=None, status=RequestStatus.UNRELEASED),
    ]

    assert (
        is_unreleased(
            request,
            media_details=details,
            local_episodes=episodes,
            today=TODAY,
        )
        is True
    )


def test_is_unreleased_tv_all_aired_downloaded_with_empty_season():
    """A series with all aired episodes downloaded but an empty future season is unreleased."""
    request = SimpleNamespace(media_type="tv", tmdb_id=456)
    details = {
        "firstAirDate": "2025-01-01",
        "status": "Returning Series",
    }
    episodes = [
        SimpleNamespace(air_date=date(2026, 4, 1), status=RequestStatus.COMPLETED),
        SimpleNamespace(air_date=date(2026, 4, 8), status=RequestStatus.COMPLETED),
    ]
    assert (
        is_unreleased(
            request,
            media_details=details,
            local_episodes=episodes,
            today=TODAY,
            has_empty_seasons=True,
        )
        is True
    )


def test_is_unreleased_tv_all_aired_downloaded_no_empty_seasons():
    """A series with all aired episodes downloaded and no empty seasons is released."""
    request = SimpleNamespace(media_type="tv", tmdb_id=456)
    details = {
        "firstAirDate": "2025-01-01",
        "status": "Returning Series",
    }
    episodes = [
        SimpleNamespace(air_date=date(2026, 4, 1), status=RequestStatus.COMPLETED),
        SimpleNamespace(air_date=date(2026, 4, 8), status=RequestStatus.COMPLETED),
    ]
    assert (
        is_unreleased(
            request,
            media_details=details,
            local_episodes=episodes,
            today=TODAY,
            has_empty_seasons=False,
        )
        is False
    )


def test_is_unreleased_tv_completed_episodes_with_future_next_episode_signal():
    request = SimpleNamespace(media_type="tv", tmdb_id=456)
    details = {
        "firstAirDate": "2025-01-01",
        "status": "Returning Series",
        "nextEpisodeToAir": {"airDate": "2026-05-01"},
    }
    episodes = [
        SimpleNamespace(air_date=date(2026, 4, 1), status=RequestStatus.COMPLETED),
        SimpleNamespace(air_date=date(2026, 4, 8), status=RequestStatus.COMPLETED),
    ]

    assert (
        is_unreleased(request, media_details=details, local_episodes=episodes, today=TODAY) is True
    )


def test_is_unreleased_false_without_tmdb_id():
    request = SimpleNamespace(media_type="movie", tmdb_id=None)
    details = {
        "status": "In Production",
        "releaseDate": "2026-08-01",
        "releases": {"results": []},
    }

    assert is_unreleased(request, media_details=details, today=TODAY) is False
