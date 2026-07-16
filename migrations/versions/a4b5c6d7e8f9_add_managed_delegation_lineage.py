"""add managed delegation lineage

Revision ID: a4b5c6d7e8f9
Revises: f3a4b5c6d7e8
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a4b5c6d7e8f9"
down_revision: str | None = "f3a4b5c6d7e8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "workflow_runs",
        sa.Column(
            "parent_workflow_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workflow_runs.id", ondelete="RESTRICT"),
        ),
    )
    op.add_column(
        "workflow_runs",
        sa.Column("root_workflow_run_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "workflow_runs",
        sa.Column("delegation_depth", sa.Integer(), nullable=False, server_default="0"),
    )
    op.execute(
        "UPDATE workflow_runs SET root_workflow_run_id = id WHERE root_workflow_run_id IS NULL"
    )
    op.create_foreign_key(
        "fk_workflow_runs_root_workflow_run_id",
        "workflow_runs",
        "workflow_runs",
        ["root_workflow_run_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.alter_column("workflow_runs", "root_workflow_run_id", nullable=False)
    op.create_index(
        "ix_workflow_runs_parent_workflow_run_id",
        "workflow_runs",
        ["parent_workflow_run_id"],
    )
    op.create_index(
        "ix_workflow_runs_root_workflow_run_id", "workflow_runs", ["root_workflow_run_id"]
    )
    op.create_table(
        "run_delegations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "parent_workflow_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workflow_runs.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "child_workflow_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workflow_runs.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "root_workflow_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workflow_runs.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("delegation_depth", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=240), nullable=False),
        sa.Column("budget_limits", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "allowed_tool_definition_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
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
        sa.UniqueConstraint("child_workflow_run_id", name="uq_run_delegation_child"),
        sa.UniqueConstraint(
            "parent_workflow_run_id", "idempotency_key", name="uq_run_delegation_parent_key"
        ),
        sa.CheckConstraint("delegation_depth >= 1", name="ck_run_delegation_depth"),
    )
    op.create_index(
        "ix_run_delegations_parent_workflow_run_id",
        "run_delegations",
        ["parent_workflow_run_id"],
    )
    op.create_index(
        "ix_run_delegations_child_workflow_run_id", "run_delegations", ["child_workflow_run_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_run_delegations_child_workflow_run_id", table_name="run_delegations")
    op.drop_index("ix_run_delegations_parent_workflow_run_id", table_name="run_delegations")
    op.drop_table("run_delegations")
    op.drop_index("ix_workflow_runs_root_workflow_run_id", table_name="workflow_runs")
    op.drop_index("ix_workflow_runs_parent_workflow_run_id", table_name="workflow_runs")
    op.drop_constraint("fk_workflow_runs_root_workflow_run_id", "workflow_runs", type_="foreignkey")
    op.drop_column("workflow_runs", "delegation_depth")
    op.drop_column("workflow_runs", "root_workflow_run_id")
    op.drop_column("workflow_runs", "parent_workflow_run_id")
