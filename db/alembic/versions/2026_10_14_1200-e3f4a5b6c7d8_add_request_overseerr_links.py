"""Link several Seerr requests to one local request.

Revision ID: e3f4a5b6c7d8
Revises: d2e3f4a5b6c7
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e3f4a5b6c7d8"
down_revision: str | None = "d2e3f4a5b6c7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "request_overseerr_links" not in inspector.get_table_names():
        op.create_table(
            "request_overseerr_links",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "request_id",
                sa.Integer(),
                sa.ForeignKey("requests.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("overseerr_request_id", sa.Integer(), nullable=False, unique=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
        op.create_index(
            "ix_request_overseerr_links_request_id",
            "request_overseerr_links",
            ["request_id"],
        )
    # Every existing linked request keeps its Seerr request as its first link.
    op.execute(
        """
        INSERT INTO request_overseerr_links (request_id, overseerr_request_id, created_at)
        SELECT MIN(id), overseerr_request_id, MIN(created_at) FROM requests
        WHERE overseerr_request_id IS NOT NULL
          AND overseerr_request_id NOT IN (
              SELECT overseerr_request_id FROM request_overseerr_links
          )
        GROUP BY overseerr_request_id
        """
    )


def downgrade() -> None:
    op.drop_index("ix_request_overseerr_links_request_id", table_name="request_overseerr_links")
    op.drop_table("request_overseerr_links")
