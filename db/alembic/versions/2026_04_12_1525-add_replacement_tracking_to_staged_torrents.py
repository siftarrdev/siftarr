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
    existing_foreign_keys = {
        fk["constrained_columns"][0]: fk for fk in inspector.get_foreign_keys("staged_torrents")
    }

    # Use batch mode for SQLite compatibility (required for foreign keys)
    with op.batch_alter_table("staged_torrents", recreate="always") as batch_op:
        if "replaced_by_id" not in existing_columns:
            batch_op.add_column(
                sa.Column("replaced_by_id", sa.Integer, nullable=True),
            )

        if "replaced_by_id" not in existing_foreign_keys:
            batch_op.create_foreign_key(
                "fk_staged_torrents_replaced_by_id_staged_torrents",
                "staged_torrents",
                ["replaced_by_id"],
                ["id"],
            )

        if "replaced_at" not in existing_columns:
            batch_op.add_column(
                sa.Column("replaced_at", sa.DateTime, nullable=True),
            )

        if "replacement_reason" not in existing_columns:
            batch_op.add_column(
                sa.Column("replacement_reason", sa.String(500), nullable=True),
            )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = inspect(conn)
    existing_columns = [c["name"] for c in inspector.get_columns("staged_torrents")]
    existing_foreign_keys = {
        fk["constrained_columns"][0]: fk for fk in inspector.get_foreign_keys("staged_torrents")
    }

    # Use batch mode for SQLite compatibility
    with op.batch_alter_table("staged_torrents", recreate="always") as batch_op:
        if "replaced_by_id" in existing_foreign_keys:
            batch_op.drop_constraint(
                "fk_staged_torrents_replaced_by_id_staged_torrents",
                type_="foreignkey",
            )

        if "replacement_reason" in existing_columns:
            batch_op.drop_column("replacement_reason")

        if "replaced_at" in existing_columns:
            batch_op.drop_column("replaced_at")

        if "replaced_by_id" in existing_columns:
            batch_op.drop_column("replaced_by_id")
