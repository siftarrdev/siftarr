"""Tests for the TV targeting Alembic follow-up migration."""

from pathlib import Path


def test_tv_targeting_migration_replaces_size_limit_mode_column():
    """Migration should add tv_target and drop size_limit_mode."""
    migration = (
        Path(__file__).parent.parent
        / "db/alembic/versions/2026_04_16_2200_replace_size_limit_mode_with_tv_targeting.py"
    )
    migration_text = migration.read_text(encoding="utf-8")

    assert '"tv_target", sa.Enum("EPISODE", "SEASON_PACK", name="tvtarget")' in migration_text
    assert 'drop_column("size_limit_mode")' in migration_text
    assert 'sa.Enum("EPISODE", "SEASON_PACK", name="tvtarget")' in migration_text
