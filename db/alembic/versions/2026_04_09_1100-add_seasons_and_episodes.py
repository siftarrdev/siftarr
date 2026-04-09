"""Add seasons and episodes tables and release TV columns.

Revision ID: add_seasons_and_episodes
Revises: add_performance_indexes
Create Date: 2026-04-09 11:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "add_seasons_and_episodes"
down_revision: str | None = "add_performance_indexes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = inspect(conn)
    existing_tables = inspector.get_table_names()

    if "seasons" not in existing_tables:
        op.create_table(
            "seasons",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("request_id", sa.Integer(), sa.ForeignKey("requests.id"), nullable=False),
            sa.Column("season_number", sa.Integer(), nullable=False),
            sa.Column(
                "status",
                sa.Enum(
                    "received",
                    "searching",
                    "pending",
                    "staged",
                    "downloading",
                    "completed",
                    "failed",
                    name="requeststatus",
                ),
                nullable=False,
            ),
            sa.Column("synced_at", sa.DateTime(), nullable=True),
            sa.UniqueConstraint("request_id", "season_number", name="uq_seasons_request_season"),
        )
        op.create_index("ix_seasons_request_id", "seasons", ["request_id"])

    if "episodes" not in existing_tables:
        op.create_table(
            "episodes",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("season_id", sa.Integer(), sa.ForeignKey("seasons.id"), nullable=False),
            sa.Column("episode_number", sa.Integer(), nullable=False),
            sa.Column("title", sa.String(500), nullable=True),
            sa.Column("air_date", sa.Date(), nullable=True),
            sa.Column(
                "status",
                sa.Enum(
                    "received",
                    "searching",
                    "pending",
                    "staged",
                    "downloading",
                    "completed",
                    "failed",
                    name="requeststatus",
                ),
                nullable=False,
            ),
            sa.Column("release_id", sa.Integer(), sa.ForeignKey("releases.id"), nullable=True),
            sa.UniqueConstraint("season_id", "episode_number", name="uq_episodes_season_episode"),
        )
        op.create_index("ix_episodes_season_id", "episodes", ["season_id"])

    if "releases" in existing_tables:
        release_columns = [c["name"] for c in inspector.get_columns("releases")]
        if "season_number" not in release_columns:
            op.add_column("releases", sa.Column("season_number", sa.Integer(), nullable=True))
        if "episode_number" not in release_columns:
            op.add_column("releases", sa.Column("episode_number", sa.Integer(), nullable=True))

        existing_indexes = [idx["name"] for idx in inspector.get_indexes("releases")]
        if "ix_releases_request_season_episode" not in existing_indexes:
            op.create_index(
                "ix_releases_request_season_episode",
                "releases",
                ["request_id", "season_number", "episode_number"],
            )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = inspect(conn)
    existing_tables = inspector.get_table_names()

    if "releases" in existing_tables:
        release_columns = [c["name"] for c in inspector.get_columns("releases")]
        if "episode_number" in release_columns:
            op.drop_column("releases", "episode_number")
        if "season_number" in release_columns:
            op.drop_column("releases", "season_number")

        existing_indexes = [idx["name"] for idx in inspector.get_indexes("releases")]
        if "ix_releases_request_season_episode" in existing_indexes:
            op.drop_index("ix_releases_request_season_episode", table_name="releases")

    existing_tables = inspector.get_table_names()
    if "episodes" in existing_tables:
        op.drop_index("ix_episodes_season_id", table_name="episodes")
        op.drop_table("episodes")

    if "seasons" in existing_tables:
        op.drop_index("ix_seasons_request_id", table_name="seasons")
        op.drop_table("seasons")
