"""Add selection source to staged torrents.

Revision ID: add_selection_source_to_staged_torrents
Revises: add_size_limit_rule_fields
Create Date: 2026-04-07 12:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "add_selection_source_to_staged_torrents"
down_revision: str | None = "add_size_limit_rule_fields"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "staged_torrents",
        sa.Column("selection_source", sa.String(length=20), nullable=False, server_default="rule"),
    )


def downgrade() -> None:
    op.drop_column("staged_torrents", "selection_source")
