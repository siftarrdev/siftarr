"""Add rejection_reason to requests.

Revision ID: add_rejection_reason_to_requests
Revises: add_selection_source_to_staged_torrents
Create Date: 2026-04-07 14:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "add_rejection_reason_to_requests"
down_revision: str | None = "add_selection_source_to_staged_torrents"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("requests", sa.Column("rejection_reason", sa.String(500), nullable=True))


def downgrade() -> None:
    op.drop_column("requests", "rejection_reason")
