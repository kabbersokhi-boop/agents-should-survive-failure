"""add recoverable workflow starts

Revision ID: 7f21c6d9a803
Revises: 6b10a9e4c751
Create Date: 2026-07-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "7f21c6d9a803"
down_revision: str | None = "6b10a9e4c751"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "workflow_runs",
        sa.Column(
            "request_fingerprint", sa.String(length=64), server_default="legacy", nullable=False
        ),
    )
    start_status = sa.Enum("PENDING", "STARTED", "FAILED", name="workflow_start_status")
    op.create_table(
        "workflow_start_attempts",
        sa.Column("workflow_run_id", sa.Uuid(), nullable=False),
        sa.Column("status", start_status, nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("error_category", sa.String(length=80), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["workflow_run_id"], ["workflow_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workflow_run_id", name="uq_workflow_start_attempt_run"),
    )


def downgrade() -> None:
    op.drop_table("workflow_start_attempts")
    sa.Enum(name="workflow_start_status").drop(op.get_bind(), checkfirst=True)
    op.drop_column("workflow_runs", "request_fingerprint")
