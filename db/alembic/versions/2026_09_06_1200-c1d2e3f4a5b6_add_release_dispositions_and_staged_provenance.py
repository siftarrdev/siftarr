"""Add durable release dispositions and staged release provenance.

Revision ID: c1d2e3f4a5b6
Revises: b7c8d9e0f1a2
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c1d2e3f4a5b6"
down_revision: str | None = "b7c8d9e0f1a2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "release_dispositions" not in inspector.get_table_names():
        op.create_table(
            "release_dispositions",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "request_id",
                sa.Integer(),
                sa.ForeignKey("requests.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("release_key", sa.String(128), nullable=False),
            sa.Column("info_hash", sa.String(64), nullable=True),
            sa.Column("target_scope", sa.String(255), nullable=False, server_default="request"),
            sa.Column("kind", sa.String(32), nullable=False, server_default="rejected"),
            sa.Column("reason", sa.Text()),
            sa.Column("staged_torrent_id", sa.Integer(), sa.ForeignKey("staged_torrents.id")),
            sa.Column("expires_at", sa.DateTime()),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint(
                "request_id", "release_key", "target_scope", name="uq_release_disposition_identity"
            ),
        )
        op.create_index(
            "ix_release_dispositions_request_id", "release_dispositions", ["request_id"]
        )
        op.create_index(
            "ix_release_dispositions_expires_at", "release_dispositions", ["expires_at"]
        )
    disposition_columns = {
        column["name"] for column in sa.inspect(op.get_bind()).get_columns("release_dispositions")
    }
    if "info_hash" not in disposition_columns:
        op.add_column("release_dispositions", sa.Column("info_hash", sa.String(64), nullable=True))
    staged_columns = {column["name"] for column in inspector.get_columns("staged_torrents")}
    for _name, column in (
        (
            "source_release_id",
            sa.Column(
                "source_release_id", sa.Integer(), sa.ForeignKey("releases.id", ondelete="SET NULL")
            ),
        ),
        ("seeders_snapshot", sa.Column("seeders_snapshot", sa.Integer())),
        ("rule_evidence_snapshot", sa.Column("rule_evidence_snapshot", sa.JSON())),
        ("rule_fingerprint", sa.Column("rule_fingerprint", sa.String(64))),
        ("target_scope", sa.Column("target_scope", sa.String(255))),
        (
            "identity_override",
            sa.Column("identity_override", sa.Boolean(), nullable=False, server_default=sa.false()),
        ),
        (
            "rules_override",
            sa.Column("rules_override", sa.Boolean(), nullable=False, server_default=sa.false()),
        ),
        (
            "seeders_override",
            sa.Column("seeders_override", sa.Boolean(), nullable=False, server_default=sa.false()),
        ),
        (
            "rejection_override",
            sa.Column(
                "rejection_override", sa.Boolean(), nullable=False, server_default=sa.false()
            ),
        ),
        (
            "replaces_id",
            sa.Column("replaces_id", sa.Integer(), sa.ForeignKey("staged_torrents.id")),
        ),
    ):
        if column.name not in staged_columns:
            if op.get_bind().dialect.name == "sqlite" and column.foreign_keys:
                # SQLite supports a nullable REFERENCES column inline, but not the
                # separate ALTER CONSTRAINT emitted by Alembic's add_column().
                # This keeps the upgrade additive without rebuilding live stages.
                target = {
                    "source_release_id": "releases(id) ON DELETE SET NULL",
                    "replaces_id": "staged_torrents(id)",
                }[column.name]
                op.execute(
                    sa.text(
                        f"ALTER TABLE staged_torrents ADD COLUMN {column.name} "
                        f"INTEGER REFERENCES {target}"
                    )
                )
            else:
                op.add_column("staged_torrents", column)


def downgrade() -> None:
    for name in (
        "replaces_id",
        "rejection_override",
        "seeders_override",
        "rules_override",
        "identity_override",
        "target_scope",
        "rule_fingerprint",
        "rule_evidence_snapshot",
        "seeders_snapshot",
        "source_release_id",
    ):
        op.drop_column("staged_torrents", name)
    op.drop_index("ix_release_dispositions_expires_at", table_name="release_dispositions")
    op.drop_index("ix_release_dispositions_request_id", table_name="release_dispositions")
    op.drop_table("release_dispositions")
