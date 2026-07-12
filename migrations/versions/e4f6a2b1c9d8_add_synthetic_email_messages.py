"""add synthetic email messages

Revision ID: e4f6a2b1c9d8
Revises: c1d4e3f9a8b7
Create Date: 2026-07-12 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e4f6a2b1c9d8"
down_revision: str | None = "c1d4e3f9a8b7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tool_invocations",
        sa.Column("correlation_id", sa.String(length=160), server_default="legacy", nullable=False),
    )
    op.create_table(
        "synthetic_email_messages",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workflow_run_id", sa.Uuid(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=240), nullable=False),
        sa.Column("recipient", sa.String(length=320), nullable=False),
        sa.Column("subject", sa.String(length=240), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
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
        sa.UniqueConstraint(
            "workflow_run_id", "idempotency_key", name="uq_synthetic_email_run_key"
        ),
    )
    op.create_index(
        "ix_synthetic_email_run_created",
        "synthetic_email_messages",
        ["workflow_run_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_synthetic_email_run_created", table_name="synthetic_email_messages")
    op.drop_table("synthetic_email_messages")
    op.drop_column("tool_invocations", "correlation_id")
