"""Add size-limit rule fields.

Revision ID: add_size_limit_rule_fields
Revises: add_media_scope_to_rules
Create Date: 2026-04-07 02:15:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "add_size_limit_rule_fields"
down_revision: str | None = "add_media_scope_to_rules"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("rules", sa.Column("min_size_gb", sa.Float(), nullable=True))
    op.add_column("rules", sa.Column("max_size_gb", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("rules", "max_size_gb")
    op.drop_column("rules", "min_size_gb")
