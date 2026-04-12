"""Add replacement tracking to staged torrents.

Revision ID: add_replacement_tracking_to_staged_torrents
Revises: add_plex_rating_key_to_requests
Create Date: 2026-04-12 15:25:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

# revision identifiers, used by Alembic.
revision: str = "add_replacement_tracking_to_staged_torrents"
down_revision: str | None = "add_plex_rating_key_to_requests"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = inspect(conn)
    existing_columns = [c["name"] for c in inspector.get_columns("staged_torrents")]

    if "replaced_by_id" not in existing_columns:
        op.add_column(
            "staged_torrents",
            sa.Column(
                "replaced_by_id", sa.Integer, sa.ForeignKey("staged_torrents.id"), nullable=True
            ),
        )

    if "replaced_at" not in existing_columns:
        op.add_column(
            "staged_torrents",
            sa.Column("replaced_at", sa.DateTime, nullable=True),
        )

    if "replacement_reason" not in existing_columns:
        op.add_column(
            "staged_torrents",
            sa.Column("replacement_reason", sa.String(500), nullable=True),
        )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = inspect(conn)
    existing_columns = [c["name"] for c in inspector.get_columns("staged_torrents")]

    if "replacement_reason" in existing_columns:
        op.drop_column("staged_torrents", "replacement_reason")

    if "replaced_at" in existing_columns:
        op.drop_column("staged_torrents", "replaced_at")

    if "replaced_by_id" in existing_columns:
        op.drop_column("staged_torrents", "replaced_by_id")
