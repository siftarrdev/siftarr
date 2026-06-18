"""Tests for database module."""

import sqlite3
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI


class TestInitDb:
    """Tests for init_db()."""

    @pytest.mark.asyncio
    async def test_creates_tables_on_fresh_database(self, tmp_path):
        """init_db should create all tables on a fresh SQLite database."""
        from app.siftarr.database import init_db

        db_path = tmp_path / "fresh.db"
        database_url = f"sqlite+aiosqlite:///{db_path}"

        with patch(
            "app.siftarr.database.get_settings",
            return_value=SimpleNamespace(database_url=database_url),
        ):
            await init_db()

        # Verify tables were created.
        connection = sqlite3.connect(db_path)
        try:
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            assert "requests" in tables
            assert "releases" in tables
            assert "rules" in tables
            assert "staged_torrents" in tables
            assert "activity_logs" in tables
            assert "seasons" in tables
            assert "episodes" in tables
            assert "app_settings" in tables
            assert "alembic_version" in tables
            release_columns = {row[1] for row in connection.execute("PRAGMA table_info(releases)")}
            assert "search_source" in release_columns
        finally:
            connection.close()

    @pytest.mark.asyncio
    async def test_is_idempotent_on_existing_database(self, tmp_path):
        """init_db should not error when called on an already-initialized database."""
        from app.siftarr.database import init_db

        db_path = tmp_path / "existing.db"
        database_url = f"sqlite+aiosqlite:///{db_path}"

        with patch(
            "app.siftarr.database.get_settings",
            return_value=SimpleNamespace(database_url=database_url),
        ):
            await init_db()
            # Second call should succeed without error.
            await init_db()

    @pytest.mark.asyncio
    async def test_skips_non_sqlite_databases(self):
        """init_db should be a no-op for non-SQLite database URLs."""
        from app.siftarr.database import init_db

        with patch(
            "app.siftarr.database.get_settings",
            return_value=SimpleNamespace(database_url="postgresql://localhost/db"),
        ):
            # Should not raise.
            await init_db()

    @pytest.mark.asyncio
    async def test_stamps_alembic_revision(self, tmp_path):
        """init_db should write the current Alembic revision to the version table."""
        from app.siftarr.database import get_alembic_head_revision, init_db

        db_path = tmp_path / "stamp.db"
        database_url = f"sqlite+aiosqlite:///{db_path}"

        with patch(
            "app.siftarr.database.get_settings",
            return_value=SimpleNamespace(database_url=database_url),
        ):
            await init_db()

        connection = sqlite3.connect(db_path)
        try:
            revision = connection.execute(
                "SELECT version_num FROM alembic_version LIMIT 1"
            ).fetchone()
            assert revision is not None
            assert revision[0] == get_alembic_head_revision()
        finally:
            connection.close()

    @pytest.mark.asyncio
    async def test_upgrades_existing_database_to_head(self, tmp_path):
        """init_db should apply migrations instead of stamping over old schemas."""
        from app.siftarr.database import get_alembic_head_revision, init_db

        db_path = tmp_path / "production.db"
        sync_url = f"sqlite:///{db_path}"
        database_url = f"sqlite+aiosqlite:///{db_path}"
        config = Config("alembic.ini")
        config.set_main_option("sqlalchemy.url", sync_url)
        command.upgrade(config, "f03b57417775")

        connection = sqlite3.connect(db_path)
        try:
            connection.execute("DROP TABLE IF EXISTS search_run_candidates")
            connection.execute("DROP TABLE IF EXISTS search_runs")
            connection.execute("ALTER TABLE releases DROP COLUMN rule_evidence")
            connection.execute("ALTER TABLE releases DROP COLUMN parse_metadata")
            connection.execute("UPDATE alembic_version SET version_num = 'f03b57417775'")
            connection.commit()
        finally:
            connection.close()

        with patch(
            "app.siftarr.database.get_settings",
            return_value=SimpleNamespace(database_url=database_url),
        ):
            await init_db()

        connection = sqlite3.connect(db_path)
        try:
            revision = connection.execute(
                "SELECT version_num FROM alembic_version LIMIT 1"
            ).fetchone()
            release_columns = {row[1] for row in connection.execute("PRAGMA table_info(releases)")}
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            assert revision == (get_alembic_head_revision(),)
            assert "rule_evidence" in release_columns
            assert "parse_metadata" in release_columns
            assert "search_runs" in tables
            assert "search_run_candidates" in tables
        finally:
            connection.close()


class TestDatabaseLifespan:
    """Tests for database integration with the FastAPI lifespan."""

    @pytest.mark.asyncio
    async def test_starts_scheduler_after_database_init(self):
        """Scheduler startup should happen after database verification."""
        from app.siftarr.main import lifespan

        events: list[str] = []
        scheduler = MagicMock()
        scheduler.start.side_effect = lambda: events.append("scheduler.start")
        scheduler.stop = MagicMock()
        init_db = AsyncMock(side_effect=lambda: events.append("init_db"))

        settings = SimpleNamespace(
            prowlarr_url="http://prowlarr",
            prowlarr_api_key="key",
            overseerr_url="http://overseerr",
            overseerr_api_key="key",
            qbittorrent_url="http://qbittorrent",
            staging_mode_enabled=False,
        )

        with (
            patch("app.siftarr.main.get_settings", return_value=settings),
            patch("app.siftarr.main._ensure_db_directory"),
            patch("app.siftarr.database.init_db", init_db),
            patch("app.siftarr.main.SchedulerService", return_value=scheduler) as scheduler_cls,
            patch("app.siftarr.main.close_shared_client", AsyncMock()),
        ):
            async with lifespan(FastAPI()):
                pass

        assert events == ["init_db", "scheduler.start"]
        scheduler_cls.assert_called_once()
        scheduler.stop.assert_called_once()

    @pytest.mark.asyncio
    async def test_does_not_start_scheduler_when_database_init_fails(self):
        """Background work must not start if database verification fails."""
        from app.siftarr.main import lifespan

        settings = SimpleNamespace(
            prowlarr_url="http://prowlarr",
            prowlarr_api_key="key",
            overseerr_url="http://overseerr",
            overseerr_api_key="key",
            qbittorrent_url="http://qbittorrent",
            staging_mode_enabled=False,
        )

        with (
            patch("app.siftarr.main.get_settings", return_value=settings),
            patch("app.siftarr.main._ensure_db_directory"),
            patch(
                "app.siftarr.database.init_db",
                AsyncMock(side_effect=RuntimeError("db not ready")),
            ),
            patch("app.siftarr.main.SchedulerService") as scheduler_cls,
            patch("app.siftarr.main.close_shared_client", AsyncMock()),
            pytest.raises(RuntimeError, match="db not ready"),
        ):
            async with lifespan(FastAPI()):
                pass

        scheduler_cls.assert_not_called()


class TestDatabaseHelpers:
    """Tests for database utility functions."""

    def test_get_sync_sqlite_url(self):
        """_get_sync_sqlite_url should strip the async driver prefix."""
        from app.siftarr.database import _get_sync_sqlite_url

        assert _get_sync_sqlite_url("sqlite+aiosqlite:///data/db.db") == "sqlite:///data/db.db"
        assert _get_sync_sqlite_url("sqlite:///data/db.db") == "sqlite:///data/db.db"

    def test_get_sqlite_db_path(self):
        """_get_sqlite_db_path should extract the file path from a SQLite URL."""
        from app.siftarr.database import _get_sqlite_db_path

        path = _get_sqlite_db_path("sqlite+aiosqlite:///data/db.db")
        assert str(path) == "data/db.db"

    def test_get_sqlite_db_path_raises_on_unsupported_url(self):
        """_get_sqlite_db_path should raise for non-SQLite URLs."""
        from app.siftarr.database import _get_sqlite_db_path

        with pytest.raises(ValueError, match="unsupported SQLite URL"):
            _get_sqlite_db_path("postgresql://localhost/db")

    def test_inspect_returns_empty_for_nonexistent_db(self):
        """_inspect_sqlite_database should return empty sets for missing files."""
        from app.siftarr.database import _inspect_sqlite_database

        tables, revision = _inspect_sqlite_database(Path("/nonexistent/path.db"))
        assert tables == set()
        assert revision is None

    def test_base_model_import(self):
        """Test Base model import."""
        from app.siftarr.models._base import Base

        assert Base is not None

    def test_current_revision_is_defined(self):
        """Current Alembic head should be discoverable dynamically."""
        from app.siftarr.database import CURRENT_ALEMBIC_REVISION, get_alembic_head_revision

        assert CURRENT_ALEMBIC_REVISION == "head"
        assert get_alembic_head_revision() == "b7c8d9e0f1a2"

    def test_alembic_history_supports_incremental_revisions(self):
        """Alembic history should include focused incremental migrations."""

        versions = list(Path("db/alembic/versions").glob("*.py"))
        assert len(versions) >= 2, versions

        revisions = {path.read_text() for path in versions}
        assert any('revision: str = "f03b57417775"' in text for text in revisions)
        assert any('down_revision: str | None = "f03b57417775"' in text for text in revisions)
        assert any('revision: str = "a1b2c3d4e5f6"' in text for text in revisions)
