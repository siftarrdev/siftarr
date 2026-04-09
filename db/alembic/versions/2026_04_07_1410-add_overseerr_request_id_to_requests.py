"""Add overseerr_request_id to requests.

Revision ID: add_overseerr_request_id_to_requests
Revises: add_rejection_reason_to_requests
Create Date: 2026-04-07 14:10:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "add_overseerr_request_id_to_requests"
down_revision: str | None = "add_rejection_reason_to_requests"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "requests",
        sa.Column("overseerr_request_id", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("requests", "overseerr_request_id")
