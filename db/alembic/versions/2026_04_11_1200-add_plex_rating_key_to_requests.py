"""Add plex_rating_key to requests table.

Revision ID: add_plex_rating_key_to_requests
Revises: add_seasons_and_episodes
Create Date: 2026-04-11 1200-00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "add_plex_rating_key_to_requests"
down_revision: str | None = "add_seasons_and_episodes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = inspect(conn)
    existing_columns = [c["name"] for c in inspector.get_columns("requests")]

    if "plex_rating_key" not in existing_columns:
        op.add_column(
            "requests",
            sa.Column("plex_rating_key", sa.String(100), nullable=True),
        )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = inspect(conn)
    existing_columns = [c["name"] for c in inspector.get_columns("requests")]

    if "plex_rating_key" in existing_columns:
        op.drop_column("requests", "plex_rating_key")
