"""wave_2_data_model_simplification

Revision ID: afaa97a66313
Revises: 057a73d850e6
Create Date: 2026-04-21 12:08:35.787681

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "afaa97a66313"
down_revision: str | None = "057a73d850e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_REQUEST_STATUS = sa.Enum(
    "RECEIVED",
    "SEARCHING",
    "PENDING",
    "UNRELEASED",
    "STAGED",
    "DOWNLOADING",
    "COMPLETED",
    "FAILED",
    "AVAILABLE",
    "PARTIALLY_AVAILABLE",
    "DENIED",
    name="requeststatus",
)
NEW_REQUEST_STATUS = sa.Enum(
    "SEARCHING",
    "PENDING",
    "UNRELEASED",
    "STAGED",
    "DOWNLOADING",
    "COMPLETED",
    "FAILED",
    "DENIED",
    name="requeststatus",
)


def _normalize_status_values() -> None:
    op.execute(sa.text("UPDATE requests SET status = 'PENDING' WHERE status = 'RECEIVED'"))
    op.execute(
        sa.text(
            "UPDATE requests SET status = 'COMPLETED' "
            "WHERE status IN ('AVAILABLE', 'PARTIALLY_AVAILABLE')"
        )
    )
    op.execute(sa.text("UPDATE seasons SET status = 'PENDING' WHERE status = 'RECEIVED'"))
    op.execute(
        sa.text(
            "UPDATE seasons SET status = 'COMPLETED' "
            "WHERE status IN ('AVAILABLE', 'PARTIALLY_AVAILABLE')"
        )
    )
    op.execute(sa.text("UPDATE episodes SET status = 'PENDING' WHERE status = 'RECEIVED'"))
    op.execute(
        sa.text(
            "UPDATE episodes SET status = 'COMPLETED' "
            "WHERE status IN ('AVAILABLE', 'PARTIALLY_AVAILABLE')"
        )
    )


def upgrade() -> None:
    _normalize_status_values()

    op.drop_index("ix_pending_queue_next_retry_at", table_name="pending_queue")
    op.drop_table("pending_queue")
    op.drop_table("plex_scan_state")
    op.drop_table("settings")

    with op.batch_alter_table("episodes", recreate="always") as batch_op:
        batch_op.alter_column(
            "status",
            existing_type=OLD_REQUEST_STATUS,
            type_=NEW_REQUEST_STATUS,
            existing_nullable=False,
        )

    with op.batch_alter_table("seasons", recreate="always") as batch_op:
        batch_op.alter_column(
            "status",
            existing_type=OLD_REQUEST_STATUS,
            type_=NEW_REQUEST_STATUS,
            existing_nullable=False,
        )

    with op.batch_alter_table("requests", recreate="always") as batch_op:
        batch_op.add_column(sa.Column("next_retry_at", sa.DateTime(), nullable=True))
        batch_op.add_column(
            sa.Column("retry_count", sa.Integer(), nullable=False, server_default=sa.text("0"))
        )
        batch_op.add_column(sa.Column("last_plex_check_at", sa.DateTime(), nullable=True))
        batch_op.alter_column(
            "status",
            existing_type=OLD_REQUEST_STATUS,
            type_=NEW_REQUEST_STATUS,
            existing_nullable=False,
        )
        batch_op.drop_column("requested_seasons")
        batch_op.drop_column("requested_episodes")


def downgrade() -> None:
    op.create_table(
        "plex_scan_state",
        sa.Column("job_name", sa.String(length=100), nullable=False),
        sa.Column("lock_owner", sa.String(length=255), nullable=True),
        sa.Column("lock_acquired_at", sa.DateTime(), nullable=True),
        sa.Column("lock_expires_at", sa.DateTime(), nullable=True),
        sa.Column("last_started_at", sa.DateTime(), nullable=True),
        sa.Column("last_finished_at", sa.DateTime(), nullable=True),
        sa.Column("last_success_at", sa.DateTime(), nullable=True),
        sa.Column("checkpoint_at", sa.DateTime(), nullable=True),
        sa.Column("last_error", sa.String(length=1000), nullable=True),
        sa.Column("metrics_payload", sa.JSON(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("job_name"),
    )
    op.create_table(
        "settings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("key", sa.String(length=100), nullable=False),
        sa.Column("value", sa.String(length=500), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("key"),
    )
    op.create_table(
        "pending_queue",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("request_id", sa.Integer(), nullable=False),
        sa.Column("retry_count", sa.Integer(), nullable=False),
        sa.Column("next_retry_at", sa.DateTime(), nullable=False),
        sa.Column("last_error", sa.String(length=1000), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["request_id"], ["requests.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("request_id"),
    )
    op.create_index(
        "ix_pending_queue_next_retry_at", "pending_queue", ["next_retry_at"], unique=False
    )
    op.execute(
        sa.text(
            "INSERT INTO pending_queue "
            "(request_id, retry_count, next_retry_at, last_error, created_at, updated_at) "
            "SELECT id, retry_count, next_retry_at, rejection_reason, updated_at, updated_at "
            "FROM requests WHERE next_retry_at IS NOT NULL"
        )
    )

    with op.batch_alter_table("requests", recreate="always") as batch_op:
        batch_op.add_column(sa.Column("requested_seasons", sa.String(length=100), nullable=True))
        batch_op.add_column(sa.Column("requested_episodes", sa.String(length=255), nullable=True))
        batch_op.alter_column(
            "status",
            existing_type=NEW_REQUEST_STATUS,
            type_=OLD_REQUEST_STATUS,
            existing_nullable=False,
        )
        batch_op.drop_column("next_retry_at")
        batch_op.drop_column("retry_count")
        batch_op.drop_column("last_plex_check_at")

    with op.batch_alter_table("seasons", recreate="always") as batch_op:
        batch_op.alter_column(
            "status",
            existing_type=NEW_REQUEST_STATUS,
            type_=OLD_REQUEST_STATUS,
            existing_nullable=False,
        )

    with op.batch_alter_table("episodes", recreate="always") as batch_op:
        batch_op.alter_column(
            "status",
            existing_type=NEW_REQUEST_STATUS,
            type_=OLD_REQUEST_STATUS,
            existing_nullable=False,
        )
