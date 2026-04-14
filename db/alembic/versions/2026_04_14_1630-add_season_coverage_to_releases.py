"""Add season coverage metadata to releases.

Revision ID: add_season_coverage_to_releases
Revises: add_size_limit_mode_to_rules
Create Date: 2026-04-14 16:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "add_season_coverage_to_releases"
down_revision: str | None = "add_size_limit_mode_to_rules"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = inspect(conn)
    existing_tables = inspector.get_table_names()
    if "releases" not in existing_tables:
        return

    release_columns = [column["name"] for column in inspector.get_columns("releases")]
    if "season_coverage" not in release_columns:
        op.add_column("releases", sa.Column("season_coverage", sa.String(length=100), nullable=True))


def downgrade() -> None:
    conn = op.get_bind()
    inspector = inspect(conn)
    existing_tables = inspector.get_table_names()
    if "releases" not in existing_tables:
        return

    release_columns = [column["name"] for column in inspector.get_columns("releases")]
    if "season_coverage" in release_columns:
        op.drop_column("releases", "season_coverage")
