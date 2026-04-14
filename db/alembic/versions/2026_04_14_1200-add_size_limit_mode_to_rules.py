"""Add size-limit mode to rules.

Revision ID: add_size_limit_mode_to_rules
Revises: add_replacement_tracking_to_staged_torrents
Create Date: 2026-04-14 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "add_size_limit_mode_to_rules"
down_revision: str | None = "add_replacement_tracking_to_staged_torrents"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    size_limit_mode = sa.Enum("TOTAL", "PER_SEASON", name="sizelimitmode")
    size_limit_mode.create(op.get_bind(), checkfirst=True)
    op.add_column(
        "rules",
        sa.Column(
            "size_limit_mode",
            size_limit_mode,
            nullable=False,
            server_default="TOTAL",
        ),
    )
    op.alter_column("rules", "size_limit_mode", server_default=None)


def downgrade() -> None:
    op.drop_column("rules", "size_limit_mode")
    sa.Enum("TOTAL", "PER_SEASON", name="sizelimitmode").drop(op.get_bind(), checkfirst=True)
