"""Add current-state qBittorrent download health observations.

Revision ID: d2e3f4a5b6c7
Revises: c1d2e3f4a5b6
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d2e3f4a5b6c7"
down_revision: str | None = "c1d2e3f4a5b6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "download_health" not in inspector.get_table_names():
        op.create_table(
            "download_health",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "staged_torrent_id",
                sa.Integer(),
                sa.ForeignKey("staged_torrents.id", ondelete="CASCADE"),
                nullable=False,
                unique=True,
            ),
            sa.Column(
                "request_id",
                sa.Integer(),
                sa.ForeignKey("requests.id", ondelete="CASCADE"),
                nullable=True,
            ),
            sa.Column("title", sa.String(500), nullable=False),
            sa.Column("info_hash", sa.String(64)),
            sa.Column("coverage_scope", sa.String(255)),
            sa.Column("status", sa.String(32), nullable=False, server_default="monitoring"),
            sa.Column("first_observed_at", sa.DateTime(), nullable=False),
            sa.Column("last_observed_at", sa.DateTime()),
            sa.Column("last_seen_at", sa.DateTime()),
            sa.Column("missing_since", sa.DateTime()),
            sa.Column("last_progress", sa.Float()),
            sa.Column("last_progress_at", sa.DateTime()),
            sa.Column("progress_started_at", sa.DateTime()),
            sa.Column("suspended_since", sa.DateTime()),
            sa.Column("suspended_seconds", sa.Float(), nullable=False, server_default="0"),
            sa.Column("last_state", sa.String(64)),
            sa.Column("qbit_added_on", sa.DateTime()),
            sa.Column("qbit_last_activity", sa.DateTime()),
            sa.Column("qbit_completed_on", sa.DateTime()),
            sa.Column("warning_at", sa.DateTime()),
            sa.Column("recovery_eligible_at", sa.DateTime()),
            sa.Column("completed_at", sa.DateTime()),
            sa.Column("recovered_at", sa.DateTime()),
            sa.Column("superseded_at", sa.DateTime()),
            sa.Column("recovery_disposition", sa.String(32)),
            sa.Column("cooldown_until", sa.DateTime()),
            sa.Column("failure_reason", sa.Text()),
            sa.Column("last_observation", sa.JSON()),
        )
    # Refresh the inspector after create_table.  The initial schema migration
    # imports the current Base metadata and may already have created this table
    # and its indexes on a fresh database.
    current_inspector = sa.inspect(op.get_bind())
    index_names = {index["name"] for index in current_inspector.get_indexes("download_health")}
    if "ix_download_health_request_id" not in index_names:
        op.create_index("ix_download_health_request_id", "download_health", ["request_id"])
    if "ix_download_health_status" not in index_names:
        op.create_index("ix_download_health_status", "download_health", ["status"])
    if "ix_download_health_last_observed_at" not in index_names:
        op.create_index(
            "ix_download_health_last_observed_at", "download_health", ["last_observed_at"]
        )


def downgrade() -> None:
    op.drop_index("ix_download_health_last_observed_at", table_name="download_health")
    op.drop_index("ix_download_health_status", table_name="download_health")
    op.drop_index("ix_download_health_request_id", table_name="download_health")
    op.drop_table("download_health")
