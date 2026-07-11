"""add governed tool contracts

Revision ID: c1d4e3f9a8b7
Revises: a81879aa36e9
Create Date: 2026-07-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c1d4e3f9a8b7"
down_revision: str | None = "a81879aa36e9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    risk_class = sa.Enum(
        "READ_ONLY",
        "REVERSIBLE_WRITE",
        "SENSITIVE_WRITE",
        "IRREVERSIBLE",
        name="tool_risk_class",
    )
    risk_class.create(op.get_bind(), checkfirst=True)
    op.add_column(
        "tool_definitions",
        sa.Column(
            "output_schema",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
    )
    op.add_column(
        "tool_definitions",
        sa.Column("risk_class", risk_class, server_default="READ_ONLY", nullable=False),
    )
    op.add_column(
        "tool_definitions",
        sa.Column("timeout_seconds", sa.Integer(), server_default="10", nullable=False),
    )
    op.add_column(
        "tool_definitions",
        sa.Column("approval_required", sa.Boolean(), server_default="false", nullable=False),
    )
    op.add_column(
        "tool_invocations",
        sa.Column(
            "argument_fingerprint", sa.String(length=64), server_default="legacy", nullable=False
        ),
    )


def downgrade() -> None:
    op.drop_column("tool_invocations", "argument_fingerprint")
    op.drop_column("tool_definitions", "approval_required")
    op.drop_column("tool_definitions", "timeout_seconds")
    op.drop_column("tool_definitions", "risk_class")
    sa.Enum(name="tool_risk_class").drop(op.get_bind(), checkfirst=True)
    op.drop_column("tool_definitions", "output_schema")
