"""add immutable run tool grant snapshots

Revision ID: f1a2b3c4d5e6
Revises: e8f1a2b3c4d5
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f1a2b3c4d5e6"
down_revision: str | None = "e8f1a2b3c4d5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_tool_grants",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "agent_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agents.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "tool_definition_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tool_definitions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("policy_version", sa.String(length=64), nullable=False),
        sa.Column("policy_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("agent_id", "tool_definition_id", name="uq_agent_tool_grant"),
    )
    op.create_table(
        "run_tool_grant_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "workflow_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workflow_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "tool_definition_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tool_definitions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("policy_version", sa.String(length=64), nullable=False),
        sa.Column("policy_hash", sa.String(length=64), nullable=False),
        sa.UniqueConstraint(
            "workflow_run_id", "tool_definition_id", name="uq_run_tool_grant_snapshot"
        ),
    )
    for table, message in (
        ("agent_tool_grants", "agent tool grants are immutable"),
        ("run_tool_grant_snapshots", "run tool grant snapshots are immutable"),
    ):
        op.execute(f"""
        CREATE FUNCTION reject_{table}_mutation() RETURNS trigger AS $$
        BEGIN RAISE EXCEPTION '{message}'; END; $$ LANGUAGE plpgsql;
        """)
        op.execute(f"""
        CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table}
        FOR EACH ROW EXECUTE FUNCTION reject_{table}_mutation();
        """)


def downgrade() -> None:
    for table in ("run_tool_grant_snapshots", "agent_tool_grants"):
        op.execute(f"DROP TRIGGER {table}_immutable ON {table}")
        op.execute(f"DROP FUNCTION reject_{table}_mutation()")
        op.drop_table(table)
