"""add denied status to requeststatus enum

Revision ID: 2026_04_17_0800
Revises: 2026_04_16_2200
Create Date: 2026-04-17 08:00:00.000000
"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "2026_04_17_0800"
down_revision: str | None = "2026_04_16_2200"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # SQLite stores enums as plain strings, so no schema change is needed.
    # The new 'denied' value is handled at the application level.
    pass


def downgrade() -> None:
    # No schema change to reverse for SQLite.
    pass
