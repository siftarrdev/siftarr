"""add_app_settings_table

Revision ID: 93a7b2f4d5c6
Revises: 5cafef6e1c04
Create Date: 2026-05-05 14:20:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "93a7b2f4d5c6"
down_revision: str | None = "5cafef6e1c04"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create app_settings table for runtime-configurable settings persistence."""
    op.create_table(
        "app_settings",
        sa.Column("key", sa.String(length=255), nullable=False),
        sa.Column("value", sa.Text(), nullable=False, server_default=""),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("key"),
    )


def downgrade() -> None:
    """Drop app_settings table."""
    op.drop_table("app_settings")
