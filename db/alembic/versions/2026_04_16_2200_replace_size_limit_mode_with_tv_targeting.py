"""replace size limit mode with tv targeting

Revision ID: 2026_04_16_2200
Revises: 70867b674c1f
Create Date: 2026-04-16 22:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "2026_04_16_2200"
down_revision: str | None = "70867b674c1f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("rules") as batch_op:
        batch_op.add_column(
            sa.Column(
                "tv_target", sa.Enum("EPISODE", "SEASON_PACK", name="tvtarget"), nullable=True
            )
        )
        batch_op.drop_column("size_limit_mode")


def downgrade() -> None:
    with op.batch_alter_table("rules") as batch_op:
        batch_op.add_column(
            sa.Column(
                "size_limit_mode",
                sa.Enum("TOTAL", "PER_SEASON", name="sizelimitmode"),
                nullable=False,
                server_default="TOTAL",
            )
        )
        batch_op.drop_column("tv_target")
