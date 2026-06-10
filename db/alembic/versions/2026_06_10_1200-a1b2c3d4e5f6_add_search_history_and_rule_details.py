"""Add search history and rule evidence.

Revision ID: a1b2c3d4e5f6
Revises: f03b57417775
Create Date: 2026-06-10 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: str | None = "f03b57417775"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("releases", sa.Column("rule_evidence", sa.JSON(), nullable=True))
    op.add_column("releases", sa.Column("parse_metadata", sa.JSON(), nullable=True))
    op.create_table(
        "search_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("request_id", sa.Integer(), sa.ForeignKey("requests.id"), nullable=False),
        sa.Column("trigger", sa.String(50), nullable=True),
        sa.Column("source", sa.String(50), nullable=True),
        sa.Column("search_mode", sa.String(50), nullable=True),
        sa.Column("scope", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("outcome", sa.String(50), nullable=True),
        sa.Column("counts", sa.JSON(), nullable=True),
        sa.Column("winner_summary", sa.JSON(), nullable=True),
        sa.Column("failure_summaries", sa.JSON(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_search_runs_request_id", "search_runs", ["request_id"])
    op.create_index("ix_search_runs_created_at", "search_runs", ["created_at"])
    op.create_index("ix_search_runs_status", "search_runs", ["status"])
    op.create_index("ix_search_runs_outcome", "search_runs", ["outcome"])
    op.create_table(
        "search_run_candidates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("search_run_id", sa.Integer(), sa.ForeignKey("search_runs.id"), nullable=False),
        sa.Column("request_id", sa.Integer(), sa.ForeignKey("requests.id"), nullable=False),
        sa.Column("stored_release_id", sa.Integer(), sa.ForeignKey("releases.id"), nullable=True),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column("summary", sa.JSON(), nullable=True),
        sa.Column("rule_evidence", sa.JSON(), nullable=True),
        sa.Column("parse_metadata", sa.JSON(), nullable=True),
        sa.Column("rejection_reason", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_search_run_candidates_search_run_id", "search_run_candidates", ["search_run_id"]
    )
    op.create_index("ix_search_run_candidates_request_id", "search_run_candidates", ["request_id"])
    op.create_index("ix_search_run_candidates_status", "search_run_candidates", ["status"])


def downgrade() -> None:
    op.drop_index("ix_search_run_candidates_status", table_name="search_run_candidates")
    op.drop_index("ix_search_run_candidates_request_id", table_name="search_run_candidates")
    op.drop_index("ix_search_run_candidates_search_run_id", table_name="search_run_candidates")
    op.drop_table("search_run_candidates")
    op.drop_index("ix_search_runs_outcome", table_name="search_runs")
    op.drop_index("ix_search_runs_status", table_name="search_runs")
    op.drop_index("ix_search_runs_created_at", table_name="search_runs")
    op.drop_index("ix_search_runs_request_id", table_name="search_runs")
    op.drop_table("search_runs")
    op.drop_column("releases", "parse_metadata")
    op.drop_column("releases", "rule_evidence")
