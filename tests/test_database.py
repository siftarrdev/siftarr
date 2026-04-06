"""Tests for database module."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestDatabaseModule:
    """Test cases for database module."""

    def test_base_model(self):
        """Test Base model import."""
        from app.arbitratarr.models._base import Base

        assert Base is not None

    def test_init_db(self):
        """Test database initialization."""
        from app.arbitratarr.database import init_db

        with patch("app.arbitratarr.database.engine") as mock_engine:
            with patch("app.arbitratarr.database.async_session_maker") as mock_maker:
                mock_session = AsyncMock()
                mock_session.__aenter__ = AsyncMock(return_value=mock_session)
                mock_session.__aexit__ = AsyncMock(return_value=None)
                mock_maker.return_value = mock_session

                import asyncio
                asyncio.run(init_db())