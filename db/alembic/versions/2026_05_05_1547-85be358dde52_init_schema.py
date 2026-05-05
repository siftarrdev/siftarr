"""Single init migration representing the full current schema.

Revision ID: 85be358dde52
Revises: None
Create Date: 2026-05-05 15:47:00.000000

"""

from collections.abc import Sequence

from alembic import op
from sqlalchemy import engine_from_config

from app.siftarr.models import Base

# revision identifiers, used by Alembic.
revision: str = "85be358dde52"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _get_alembic_url() -> str:
    """Read the database URL from the Alembic configuration."""
    cfg = op.get_context().config
    assert cfg is not None
    url = cfg.get_main_option("sqlalchemy.url")
    assert url is not None
    return url


def upgrade() -> None:
    """Create all tables from the current model definitions."""
    url = _get_alembic_url()
    engine = engine_from_config({"sqlalchemy.url": url}, prefix="sqlalchemy.")
    with engine.begin() as connection:
        Base.metadata.create_all(connection)


def downgrade() -> None:
    """Drop all tables."""
    url = _get_alembic_url()
    engine = engine_from_config({"sqlalchemy.url": url}, prefix="sqlalchemy.")
    with engine.begin() as connection:
        Base.metadata.drop_all(connection)
