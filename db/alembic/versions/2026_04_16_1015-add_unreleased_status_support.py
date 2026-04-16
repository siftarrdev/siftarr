"""Add unreleased status support to request status schemas.

Revision ID: add_unreleased_status_support
Revises: add_season_coverage_to_releases
Create Date: 2026-04-16 10:15:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "add_unreleased_status_support"
down_revision: str | None = "add_season_coverage_to_releases"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_REQUESTSTATUS_VALUES = (
    "RECEIVED",
    "SEARCHING",
    "PENDING",
    "UNRELEASED",
    "STAGED",
    "DOWNLOADING",
    "COMPLETED",
    "FAILED",
    "AVAILABLE",
    "PARTIALLY_AVAILABLE",
)
_STATUS_TABLES = ("requests", "seasons", "episodes")


def _sqlite_table_sql(bind, table_name: str) -> str:
    row = bind.execute(
        sa.text("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = :table_name"),
        {"table_name": table_name},
    ).fetchone()
    return str(row[0] or "") if row else ""


def _sqlite_status_allows_unreleased(bind, table_name: str) -> bool:
    return "UNRELEASED" in _sqlite_table_sql(bind, table_name).upper()


def _rebuild_sqlite_status_table(table_name: str, bind) -> None:
    inspector = inspect(bind)
    if table_name not in inspector.get_table_names():
        return
    if _sqlite_status_allows_unreleased(bind, table_name):
        return

    with op.batch_alter_table(table_name, recreate="always") as batch_op:
        batch_op.alter_column(
            "status",
            existing_type=sa.Enum(name="requeststatus"),
            type_=sa.Enum(*_REQUESTSTATUS_VALUES, name="requeststatus"),
            existing_nullable=False,
        )


def _upgrade_postgresql_enum(bind) -> None:
    bind.execute(sa.text("ALTER TYPE requeststatus ADD VALUE IF NOT EXISTS 'UNRELEASED'"))


def _upgrade_generic_enum(bind) -> None:
    enum_type = sa.Enum(*_REQUESTSTATUS_VALUES, name="requeststatus")
    enum_type.create(bind, checkfirst=True)


def upgrade() -> None:
    bind = op.get_bind()
    dialect_name = bind.dialect.name

    if dialect_name == "sqlite":
        for table_name in _STATUS_TABLES:
            _rebuild_sqlite_status_table(table_name, bind)
        return

    if dialect_name == "postgresql":
        _upgrade_postgresql_enum(bind)
        return

    _upgrade_generic_enum(bind)


def downgrade() -> None:
    # No safe automatic downgrade path for enum value removal.
    return None
