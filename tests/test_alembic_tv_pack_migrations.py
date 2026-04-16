"""Tests for the TV targeting Alembic follow-up migration."""

from pathlib import Path


def test_tv_targeting_migration_replaces_size_limit_mode_column():
    """Migration should add tv_target and drop size_limit_mode."""
    migration = Path(
        "/home/lucas/9999-personal/siftarr/db/alembic/versions/2026_04_16_2200_replace_size_limit_mode_with_tv_targeting.py"
    ).read_text(encoding="utf-8")

    assert '"tv_target", sa.Enum("EPISODE", "SEASON_PACK", name="tvtarget")' in migration
    assert 'drop_column("size_limit_mode")' in migration
    assert 'sa.Enum("EPISODE", "SEASON_PACK", name="tvtarget")' in migration
