"""pin tool versions per workflow run

Revision ID: c4d7e8f9a0b1
Revises: f0b2c4d6e8a0
Create Date: 2026-07-15 00:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c4d7e8f9a0b1"
down_revision: str | None = "f0b2c4d6e8a0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tool_run_bindings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("workflow_run_id", sa.Uuid(), nullable=False),
        sa.Column("tool_name", sa.String(length=120), nullable=False),
        sa.Column("tool_definition_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tool_definition_id"], ["tool_definitions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["workflow_run_id"], ["workflow_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workflow_run_id", "tool_name", name="uq_tool_run_binding_name"),
    )


def downgrade() -> None:
    op.drop_table("tool_run_bindings")
