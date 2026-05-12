"""Add immutable stats metrics tables.

Revision ID: a731d83b414f
Revises: f03b57417775
Create Date: 2026-05-13 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a731d83b414f"
down_revision: str | None = "f03b57417775"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "stats_release_facts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("request_id", sa.Integer(), nullable=False),
        sa.Column("release_id", sa.Integer(), nullable=True),
        sa.Column("staged_torrent_id", sa.Integer(), nullable=True),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("indexer", sa.String(length=255), nullable=True),
        sa.Column("resolution", sa.String(length=20), nullable=True),
        sa.Column("resolution_bucket", sa.String(length=20), nullable=True),
        sa.Column("selection_source", sa.String(length=20), nullable=False),
        sa.Column("approved_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["release_id"], ["releases.id"]),
        sa.ForeignKeyConstraint(["request_id"], ["requests.id"]),
        sa.ForeignKeyConstraint(["staged_torrent_id"], ["staged_torrents.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_stats_release_facts_approved_at", "stats_release_facts", ["approved_at"])
    op.create_index("ix_stats_release_facts_indexer", "stats_release_facts", ["indexer"])
    op.create_index("ix_stats_release_facts_request_id", "stats_release_facts", ["request_id"])

    op.create_table(
        "stats_rule_outcomes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("request_id", sa.Integer(), nullable=False),
        sa.Column("release_id", sa.Integer(), nullable=True),
        sa.Column("release_title", sa.String(length=500), nullable=False),
        sa.Column("rule_id", sa.Integer(), nullable=True),
        sa.Column("rule_name", sa.String(length=255), nullable=False),
        sa.Column("matched", sa.Integer(), nullable=False),
        sa.Column("outcome", sa.String(length=30), nullable=False),
        sa.Column("score_delta", sa.Integer(), nullable=False),
        sa.Column("rejection_reason", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["release_id"], ["releases.id"]),
        sa.ForeignKeyConstraint(["request_id"], ["requests.id"]),
        sa.ForeignKeyConstraint(["rule_id"], ["rules.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_stats_rule_outcomes_created_at", "stats_rule_outcomes", ["created_at"])
    op.create_index("ix_stats_rule_outcomes_request_id", "stats_rule_outcomes", ["request_id"])
    op.create_index("ix_stats_rule_outcomes_rule_id", "stats_rule_outcomes", ["rule_id"])

    op.create_table(
        "stats_timing_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("request_id", sa.Integer(), nullable=True),
        sa.Column("activity_log_id", sa.Integer(), nullable=True),
        sa.Column("event_name", sa.String(length=50), nullable=False),
        sa.Column("correlation_id", sa.String(length=100), nullable=True),
        sa.Column("duration_ms", sa.Float(), nullable=True),
        sa.Column("details", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["activity_log_id"], ["activity_logs.id"]),
        sa.ForeignKeyConstraint(["request_id"], ["requests.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_stats_timing_events_created_at", "stats_timing_events", ["created_at"])
    op.create_index("ix_stats_timing_events_event_name", "stats_timing_events", ["event_name"])
    op.create_index("ix_stats_timing_events_request_id", "stats_timing_events", ["request_id"])


def downgrade() -> None:
    op.drop_table("stats_timing_events")
    op.drop_table("stats_rule_outcomes")
    op.drop_table("stats_release_facts")
