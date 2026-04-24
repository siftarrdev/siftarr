"""Tests for database module."""

import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI


class TestDatabaseModule:
    """Test cases for database module."""

    def test_base_model(self):
        """Test Base model import."""
        from app.siftarr.models._base import Base

        assert Base is not None

    @pytest.mark.asyncio
    async def test_init_db_allows_ready_schema_without_recreating_tables(self):
        """init_db should treat an already-usable schema as ready."""
        from app.siftarr.database import CURRENT_ALEMBIC_REVISION, EXPECTED_SCHEMA_TABLES, init_db

        with (
            patch(
                "app.siftarr.database.get_settings",
                return_value=SimpleNamespace(database_url="sqlite+aiosqlite:///./data/db/test.db"),
            ),
            patch(
                "app.siftarr.database._inspect_sqlite_database",
                return_value=(
                    set(EXPECTED_SCHEMA_TABLES) | {"alembic_version"},
                    CURRENT_ALEMBIC_REVISION,
                ),
            ) as inspect_db,
            patch("app.siftarr.database._sqlite_schema_matches_expected", return_value=True),
        ):
            await init_db()

        inspect_db.assert_called_once()

    @pytest.mark.asyncio
    async def test_init_db_allows_fresh_alembic_migrated_sqlite_db(self, tmp_path):
        """A freshly migrated SQLite DB should pass startup verification."""
        from app.siftarr.database import _run_alembic_upgrade, init_db

        db_path = tmp_path / "migrated.db"
        database_url = f"sqlite+aiosqlite:///{db_path}"
        _run_alembic_upgrade(database_url)

        with patch(
            "app.siftarr.database.get_settings",
            return_value=SimpleNamespace(database_url=database_url),
        ):
            await init_db()

    @pytest.mark.asyncio
    async def test_init_db_fails_fast_when_schema_still_needs_migration(self):
        """init_db should fail when startup repair has not produced a usable schema."""
        from app.siftarr.database import init_db

        with (
            patch(
                "app.siftarr.database.get_settings",
                return_value=SimpleNamespace(database_url="sqlite+aiosqlite:///./data/db/test.db"),
            ),
            patch(
                "app.siftarr.database._inspect_sqlite_database",
                return_value=(set(), None),
            ),
            pytest.raises(RuntimeError, match="action=upgrade"),
        ):
            await init_db()

    @pytest.mark.asyncio
    async def test_init_db_fails_when_table_names_match_but_schema_has_drift(self, tmp_path):
        """init_db should reject drifted schemas even when table names look complete."""
        from app.siftarr.database import CURRENT_ALEMBIC_REVISION, init_db

        db_path = tmp_path / "drifted.db"
        with sqlite3.connect(db_path) as connection:
            connection.execute("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
            connection.execute(
                "INSERT INTO alembic_version (version_num) VALUES (?)",
                (CURRENT_ALEMBIC_REVISION,),
            )
            connection.executescript(
                """
                CREATE TABLE requests (id INTEGER PRIMARY KEY, external_id VARCHAR(255) NOT NULL);
                CREATE TABLE rules (id INTEGER PRIMARY KEY);
                CREATE TABLE staged_torrents (id INTEGER PRIMARY KEY);
                CREATE TABLE activity_logs (id INTEGER PRIMARY KEY);
                CREATE TABLE releases (id INTEGER PRIMARY KEY);
                CREATE TABLE seasons (id INTEGER PRIMARY KEY);
                CREATE TABLE episodes (id INTEGER PRIMARY KEY);
                """
            )

        with (
            patch(
                "app.siftarr.database.get_settings",
                return_value=SimpleNamespace(database_url=f"sqlite+aiosqlite:///{db_path}"),
            ),
            pytest.raises(RuntimeError, match="schema tables exist but definitions drift"),
        ):
            await init_db()

    @pytest.mark.asyncio
    async def test_lifespan_starts_scheduler_only_after_database_verification(self):
        """Scheduler startup should wait for database readiness verification."""
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
            patch("app.siftarr.main.init_db", init_db),
            patch("app.siftarr.main.SchedulerService", return_value=scheduler) as scheduler_cls,
            patch("app.siftarr.main.close_shared_client", AsyncMock()),
        ):
            async with lifespan(FastAPI()):
                pass

        assert events == ["init_db", "scheduler.start"]
        scheduler_cls.assert_called_once()
        scheduler.stop.assert_called_once()

    @pytest.mark.asyncio
    async def test_lifespan_does_not_start_scheduler_when_database_verification_fails(self):
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
                "app.siftarr.main.init_db",
                AsyncMock(side_effect=RuntimeError("db not ready")),
            ),
            patch("app.siftarr.main.SchedulerService") as scheduler_cls,
            patch("app.siftarr.main.close_shared_client", AsyncMock()),
            pytest.raises(RuntimeError, match="db not ready"),
        ):
            async with lifespan(FastAPI()):
                pass

        scheduler_cls.assert_not_called()

    def test_repair_plan_uses_upgrade_for_fresh_db(self):
        """Fresh SQLite DBs should be migrated from scratch."""
        from app.siftarr.database import (
            DatabaseRepairAction,
            determine_sqlite_repair_plan,
        )

        plan = determine_sqlite_repair_plan(table_names=set(), alembic_revision=None)

        assert plan.action == DatabaseRepairAction.UPGRADE

    def test_repair_plan_uses_stamp_for_matching_schema_without_history(self):
        """Matching schema without valid history should be stamped."""
        from app.siftarr.database import (
            EXPECTED_SCHEMA_TABLES,
            DatabaseRepairAction,
            determine_sqlite_repair_plan,
        )

        plan = determine_sqlite_repair_plan(
            table_names=set(EXPECTED_SCHEMA_TABLES),
            alembic_revision=None,
        )

        assert plan.action == DatabaseRepairAction.STAMP_HEAD

    def test_repair_plan_uses_stamp_for_matching_schema_with_stale_history(self):
        """Matching schema with stale history should be restamped."""
        from app.siftarr.database import (
            EXPECTED_SCHEMA_TABLES,
            DatabaseRepairAction,
            determine_sqlite_repair_plan,
        )

        plan = determine_sqlite_repair_plan(
            table_names=set(EXPECTED_SCHEMA_TABLES) | {"alembic_version"},
            alembic_revision="deadbeef",
        )

        assert plan.action == DatabaseRepairAction.STAMP_HEAD

    def test_repair_plan_resets_when_head_revision_has_missing_tables(self):
        """Missing tables should fail readiness even if history says head."""
        from app.siftarr.database import (
            CURRENT_ALEMBIC_REVISION,
            DatabaseRepairAction,
            determine_sqlite_repair_plan,
        )

        plan = determine_sqlite_repair_plan(
            table_names={"alembic_version"},
            alembic_revision=CURRENT_ALEMBIC_REVISION,
        )

        assert plan.action == DatabaseRepairAction.RESET

    def test_repair_plan_resets_when_only_stale_alembic_history_exists(self):
        """Broken history without schema tables should not attempt a plain upgrade."""
        from app.siftarr.database import (
            DatabaseRepairAction,
            determine_sqlite_repair_plan,
        )

        plan = determine_sqlite_repair_plan(
            table_names={"alembic_version"},
            alembic_revision="deadbeef",
        )

        assert plan.action == DatabaseRepairAction.RESET

    def test_repair_plan_resets_matching_table_names_when_schema_drift_detected(self):
        """Matching table names still require reset when definitions drift."""
        from app.siftarr.database import (
            EXPECTED_SCHEMA_TABLES,
            DatabaseRepairAction,
            determine_sqlite_repair_plan,
        )

        plan = determine_sqlite_repair_plan(
            table_names=set(EXPECTED_SCHEMA_TABLES) | {"alembic_version"},
            alembic_revision="deadbeef",
            schema_matches_expected=False,
        )

        assert plan.action == DatabaseRepairAction.RESET

    def test_repair_plan_resets_unknown_revision_with_partial_schema(self):
        """Partial drift should not be repaired in place."""
        from app.siftarr.database import (
            DatabaseRepairAction,
            determine_sqlite_repair_plan,
        )

        plan = determine_sqlite_repair_plan(
            table_names={"requests", "rules", "staged_torrents"},
            alembic_revision="deadbeef",
        )

        assert plan.action == DatabaseRepairAction.RESET

    def test_repair_plan_is_noop_for_current_schema_and_revision(self):
        """Head schema with head stamp should be left alone."""
        from app.siftarr.database import (
            CURRENT_ALEMBIC_REVISION,
            EXPECTED_SCHEMA_TABLES,
            DatabaseRepairAction,
            determine_sqlite_repair_plan,
        )

        plan = determine_sqlite_repair_plan(
            table_names=set(EXPECTED_SCHEMA_TABLES) | {"alembic_version"},
            alembic_revision=CURRENT_ALEMBIC_REVISION,
        )

        assert plan.action == DatabaseRepairAction.NOOP

    def test_prepare_sqlite_database_resets_stale_history_without_schema(self, tmp_path):
        """Startup repair should reset broken Alembic history before migrating."""
        from app.siftarr.database import (
            DatabaseRepairAction,
            prepare_sqlite_database_for_startup,
        )

        db_path = tmp_path / "broken-history.db"
        with sqlite3.connect(db_path) as connection:
            connection.execute("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
            connection.execute("INSERT INTO alembic_version (version_num) VALUES ('deadbeef')")

        with (
            patch("app.siftarr.database._delete_sqlite_database_files") as delete_db,
            patch("app.siftarr.database._run_alembic_upgrade") as upgrade,
        ):
            plan = prepare_sqlite_database_for_startup(f"sqlite+aiosqlite:///{db_path}")

        assert plan.action == DatabaseRepairAction.RESET
        delete_db.assert_called_once_with(db_path)
        upgrade.assert_called_once_with(f"sqlite+aiosqlite:///{db_path}")
