"""Add media_scope to rules.

Revision ID: add_media_scope_to_rules
Revises: bc9c8cfbe08b
Create Date: 2026-04-07 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "add_media_scope_to_rules"
down_revision: str | None = "bc9c8cfbe08b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "rules",
        sa.Column("media_scope", sa.String(length=20), nullable=False, server_default="both"),
    )


def downgrade() -> None:
    op.drop_column("rules", "media_scope")
