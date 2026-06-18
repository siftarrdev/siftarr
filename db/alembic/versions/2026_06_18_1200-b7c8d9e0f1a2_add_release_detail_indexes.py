"""Add release detail composite indexes.

Revision ID: b7c8d9e0f1a2
Revises: a1b2c3d4e5f6
Create Date: 2026-06-18 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b7c8d9e0f1a2"
down_revision: str | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    existing = {idx["name"] for idx in sa.inspect(op.get_bind()).get_indexes("releases")}
    for name, columns in {
        "ix_releases_request_score": ["request_id", "score"],
        "ix_releases_request_seeders": ["request_id", "seeders"],
        "ix_releases_request_publish_date": ["request_id", "publish_date"],
        "ix_releases_request_resolution": ["request_id", "resolution"],
    }.items():
        if name not in existing:
            op.create_index(name, "releases", columns)


def downgrade() -> None:
    existing = {idx["name"] for idx in sa.inspect(op.get_bind()).get_indexes("releases")}
    for name in (
        "ix_releases_request_resolution",
        "ix_releases_request_publish_date",
        "ix_releases_request_seeders",
        "ix_releases_request_score",
    ):
        if name in existing:
            op.drop_index(name, table_name="releases")
