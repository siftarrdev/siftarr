"""Focused tests for TV pack Alembic migrations."""

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock


def _load_migration_module(filename: str, module_name: str):
    migration_path = Path("/home/lucas/9999-personal/siftarr/db/alembic/versions") / filename
    spec = importlib.util.spec_from_file_location(module_name, migration_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


add_size_limit_mode_to_rules = _load_migration_module(
    "2026_04_14_1200-add_size_limit_mode_to_rules.py",
    "add_size_limit_mode_to_rules",
)
add_unreleased_status_support = _load_migration_module(
    "2026_04_16_1015-add_unreleased_status_support.py",
    "add_unreleased_status_support",
)


class TestAddSizeLimitModeMigration:
    def test_upgrade_skips_add_column_when_column_already_exists(self, monkeypatch):
        """Upgrade should be idempotent when legacy repair already created the column."""

        inspector = MagicMock()
        inspector.get_table_names.return_value = ["rules"]
        inspector.get_columns.return_value = [
            {"name": "id"},
            {"name": "name"},
            {"name": "size_limit_mode"},
        ]

        bind = MagicMock()
        add_column = MagicMock()
        alter_column = MagicMock()

        monkeypatch.setattr(add_size_limit_mode_to_rules, "inspect", lambda _: inspector)
        monkeypatch.setattr(add_size_limit_mode_to_rules.op, "get_bind", lambda: bind)
        monkeypatch.setattr(add_size_limit_mode_to_rules.op, "add_column", add_column)
        monkeypatch.setattr(add_size_limit_mode_to_rules.op, "alter_column", alter_column)

        created_enums = []

        class FakeEnum:
            def __init__(self, *args, **kwargs):
                self.args = args
                self.kwargs = kwargs

            def create(self, passed_bind, checkfirst=False):
                created_enums.append((passed_bind, checkfirst))

            def drop(self, passed_bind, checkfirst=False):
                created_enums.append((passed_bind, checkfirst, "drop"))

        monkeypatch.setattr(add_size_limit_mode_to_rules.sa, "Enum", FakeEnum)

        add_size_limit_mode_to_rules.upgrade()

        assert created_enums == [(bind, True)]
        add_column.assert_not_called()
        alter_column.assert_not_called()


class TestAddUnreleasedStatusSupportMigration:
    def test_revision_links_to_latest_prior_head(self):
        """Migration should continue the existing chain instead of creating another head."""
        assert add_unreleased_status_support.down_revision == "add_season_coverage_to_releases"

    def test_upgrade_creates_requeststatus_enum_with_unreleased_for_non_sqlite(self, monkeypatch):
        """Migration should include the unreleased enum value on native-enum databases."""
        bind = MagicMock()
        bind.dialect.name = "postgresql"
        executed_sql = []

        monkeypatch.setattr(add_unreleased_status_support.op, "get_bind", lambda: bind)
        monkeypatch.setattr(
            bind, "execute", lambda statement, *args, **kwargs: executed_sql.append(str(statement))
        )

        add_unreleased_status_support.upgrade()

        assert executed_sql == ["ALTER TYPE requeststatus ADD VALUE IF NOT EXISTS 'UNRELEASED'"]

    def test_upgrade_is_noop_for_sqlite(self, monkeypatch):
        """SQLite should rebuild constrained status tables to include unreleased."""
        bind = MagicMock()
        bind.dialect.name = "sqlite"
        inspector = MagicMock()
        inspector.get_table_names.return_value = ["requests", "seasons", "episodes"]
        rebuilt_tables = []

        monkeypatch.setattr(add_unreleased_status_support.op, "get_bind", lambda: bind)
        monkeypatch.setattr(add_unreleased_status_support, "inspect", lambda _: inspector)
        monkeypatch.setattr(
            add_unreleased_status_support,
            "_sqlite_status_allows_unreleased",
            lambda bind, table_name: False,
        )

        class FakeBatch:
            def __init__(self, table_name, recreate):
                self.table_name = table_name
                self.recreate = recreate

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def alter_column(self, column_name, **kwargs):
                rebuilt_tables.append((self.table_name, column_name, kwargs["existing_nullable"]))

        monkeypatch.setattr(
            add_unreleased_status_support.op,
            "batch_alter_table",
            lambda table_name, recreate: FakeBatch(table_name, recreate),
        )

        add_unreleased_status_support.upgrade()

        assert rebuilt_tables == [
            ("requests", "status", False),
            ("seasons", "status", False),
            ("episodes", "status", False),
        ]

    def test_sqlite_status_allows_unreleased_checks_table_sql(self, monkeypatch):
        """SQLite helper should detect whether the recreated check already allows unreleased."""
        bind = MagicMock()
        bind.execute.return_value.fetchone.return_value = (
            "CREATE TABLE requests (status VARCHAR CHECK (status IN ('PENDING','UNRELEASED')))",
        )

        assert (
            add_unreleased_status_support._sqlite_status_allows_unreleased(bind, "requests") is True
        )
