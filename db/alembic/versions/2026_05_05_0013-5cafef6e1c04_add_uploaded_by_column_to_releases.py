"""add_uploaded_by_column_to_releases

Revision ID: 5cafef6e1c04
Revises: 67000ee5b7cd
Create Date: 2026-05-05 00:13:38.892682

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "5cafef6e1c04"
down_revision: str | None = "67000ee5b7cd"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add uploaded_by column to releases table."""
    op.add_column("releases", sa.Column("uploaded_by", sa.String(length=255), nullable=True))


def downgrade() -> None:
    """Remove uploaded_by column from releases table."""
    op.drop_column("releases", "uploaded_by")
