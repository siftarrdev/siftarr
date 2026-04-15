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
