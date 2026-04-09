"""Add seasons and episodes tables and release TV columns.

Revision ID: add_seasons_and_episodes
Revises: add_performance_indexes
Create Date: 2026-04-09 11:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "add_seasons_and_episodes"
down_revision: str | None = "add_performance_indexes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
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

    op.create_index(
        "ix_releases_request_season_episode",
        "releases",
        ["request_id", "season_number", "episode_number"],
    )
    op.add_column("releases", sa.Column("season_number", sa.Integer(), nullable=True))
    op.add_column("releases", sa.Column("episode_number", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("releases", "episode_number")
    op.drop_column("releases", "season_number")
    op.drop_index("ix_releases_request_season_episode", table_name="releases")

    op.drop_index("ix_episodes_season_id", table_name="episodes")
    op.drop_table("episodes")

    op.drop_index("ix_seasons_request_id", table_name="seasons")
    op.drop_table("seasons")
