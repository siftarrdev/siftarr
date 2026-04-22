"""Shared fixtures for dashboard router tests."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import BackgroundTasks

from app.siftarr.services import dashboard_service
from app.siftarr.services.background_tasks import DETAILS_SYNC_TASKS


@pytest.fixture(autouse=True)
def _mock_activity_log_service(monkeypatch):
    """Patch ActivityLogService so timeline queries don't consume db.execute mocks."""
    mock_cls = MagicMock()
    mock_instance = mock_cls.return_value
    mock_instance.get_timeline = AsyncMock(return_value=[])
    monkeypatch.setattr(dashboard_service, "ActivityLogService", mock_cls)


@pytest.fixture
def mock_db():
    """Create a mock database session."""
    return AsyncMock()


@pytest.fixture
def background_tasks():
    return BackgroundTasks()


@pytest.fixture(autouse=True)
def clear_details_sync_tasks():
    DETAILS_SYNC_TASKS.clear()
    yield
    DETAILS_SYNC_TASKS.clear()


@pytest.fixture
def dashboard_template_path() -> Path:
    return Path(__file__).resolve().parents[3] / "app/siftarr/templates/dashboard.html"
