"""add high-value refund workflow projections"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c2d3e4f5a6b7"
down_revision: str | None = "b5c6d7e8f9a0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    def common() -> list[sa.Column]:
        return [
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("workflow_run_id", sa.Uuid(), nullable=False),
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
        ]

    op.create_table(
        "refund_decisions",
        *common(),
        sa.Column("refund_id", sa.String(120), nullable=False),
        sa.Column("order_id", sa.String(120), nullable=False),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("decision", sa.String(40), nullable=False),
        sa.Column("risk_score", sa.Integer(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("idempotency_key", sa.String(240), nullable=False),
        sa.UniqueConstraint("workflow_run_id", "idempotency_key", name="uq_refund_decision_key"),
    )
    op.create_table(
        "refund_projections",
        *common(),
        sa.Column("refund_id", sa.String(120), nullable=False),
        sa.Column("order_id", sa.String(120), nullable=False),
        sa.Column("customer_id", sa.String(120), nullable=False),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("idempotency_key", sa.String(240), nullable=False, unique=True),
        sa.UniqueConstraint("refund_id", name="uq_refund_projection_refund"),
    )
    op.create_table(
        "refund_emails",
        *common(),
        sa.Column("customer_id", sa.String(120), nullable=False),
        sa.Column("idempotency_key", sa.String(240), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.UniqueConstraint("workflow_run_id", "idempotency_key", name="uq_refund_email_key"),
    )


def downgrade() -> None:
    op.drop_table("refund_emails")
    op.drop_table("refund_projections")
    op.drop_table("refund_decisions")
