"""Add performance indexes for hot filter columns.

Revision ID: add_performance_indexes
Revises: add_overseerr_request_id_to_requests
Create Date: 2026-04-09 10:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

revision: str = "add_performance_indexes"
down_revision: str | None = "add_overseerr_request_id_to_requests"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index("ix_requests_status", "requests", ["status"])
    op.create_index("ix_requests_media_type", "requests", ["media_type"])
    op.create_index("ix_requests_created_at", "requests", ["created_at"])
    op.create_index("ix_releases_request_id", "releases", ["request_id"])
    op.create_index("ix_releases_score", "releases", ["score"])
    op.create_index("ix_pending_queue_next_retry_at", "pending_queue", ["next_retry_at"])


def downgrade() -> None:
    op.drop_index("ix_pending_queue_next_retry_at", table_name="pending_queue")
    op.drop_index("ix_releases_score", table_name="releases")
    op.drop_index("ix_releases_request_id", table_name="releases")
    op.drop_index("ix_requests_created_at", table_name="requests")
    op.drop_index("ix_requests_media_type", table_name="requests")
    op.drop_index("ix_requests_status", table_name="requests")
