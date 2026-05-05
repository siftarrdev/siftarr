"""add_perf_indexes

Revision ID: 3e80f5eb8ad3
Revises: 85be358dde52
Create Date: 2026-05-05 14:51:55.154102

"""

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

# revision identifiers, used by Alembic.
revision: str = "3e80f5eb8ad3"
down_revision: str | None = "85be358dde52"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The init migration (85be358dde52) uses Base.metadata.create_all(), which
# creates indexes from the current model __table_args__.  On a fresh database,
# these indexes may already exist after the init migration runs.  We check
# sqlite_master to make this migration idempotent regardless of database age.
_EXISTING_INDEXES: set[str] | None = None


def _index_exists(name: str) -> bool:
    global _EXISTING_INDEXES
    if _EXISTING_INDEXES is None:
        conn = op.get_bind()
        rows = conn.execute(text("SELECT name FROM sqlite_master WHERE type='index'")).fetchall()
        _EXISTING_INDEXES = {row[0] for row in rows}
    return name in _EXISTING_INDEXES


def _create_index_if_missing(name: str, table: str, columns: list[str]) -> None:
    if not _index_exists(name):
        op.create_index(name, table, columns, unique=False)


def _drop_index_if_exists(name: str, table: str) -> None:
    if _index_exists(name):
        op.drop_index(name, table_name=table)


def upgrade() -> None:
    _create_index_if_missing("ix_activity_logs_event_type", "activity_logs", ["event_type"])
    _create_index_if_missing("ix_rules_enabled", "rules", ["is_enabled"])
    _create_index_if_missing("ix_rules_type", "rules", ["rule_type"])
    _create_index_if_missing("ix_staged_torrents_request_id", "staged_torrents", ["request_id"])
    _create_index_if_missing("ix_staged_torrents_status", "staged_torrents", ["status"])


def downgrade() -> None:
    _drop_index_if_exists("ix_staged_torrents_status", "staged_torrents")
    _drop_index_if_exists("ix_staged_torrents_request_id", "staged_torrents")
    _drop_index_if_exists("ix_rules_type", "rules")
    _drop_index_if_exists("ix_rules_enabled", "rules")
    _drop_index_if_exists("ix_activity_logs_event_type", "activity_logs")
