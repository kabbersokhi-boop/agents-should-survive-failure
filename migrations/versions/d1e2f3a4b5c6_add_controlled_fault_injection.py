"""add controlled fault injection

Revision ID: d1e2f3a4b5c6
Revises: b3c4d5e6f7a8
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d1e2f3a4b5c6"
down_revision: str | None = "b3c4d5e6f7a8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    fault_plan_status = postgresql.ENUM(
        "active", "exhausted", "cleared", name="fault_plan_status", create_type=False
    )
    fault_plan_status.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "fault_injection_plans",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("fault_point", sa.String(length=160), nullable=False),
        sa.Column("scope_key", sa.String(length=240), nullable=False, server_default="global"),
        sa.Column("category", sa.String(length=80), nullable=False),
        sa.Column("trigger_count", sa.Integer(), nullable=False),
        sa.Column("remaining_triggers", sa.Integer(), nullable=False),
        sa.Column("delay_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", fault_plan_status, nullable=False),
        sa.Column(
            "safe_metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
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
        sa.UniqueConstraint("fault_point", "scope_key", name="uq_fault_injection_plan_scope"),
        sa.CheckConstraint("trigger_count >= 1", name="ck_fault_plan_trigger_count"),
        sa.CheckConstraint("remaining_triggers >= 0", name="ck_fault_plan_remaining_triggers"),
        sa.CheckConstraint("delay_ms >= 0", name="ck_fault_plan_delay_ms"),
    )
    op.create_index(
        "ix_fault_injection_plans_active",
        "fault_injection_plans",
        ["status", "fault_point"],
        unique=False,
    )
    op.create_table(
        "fault_injection_consumptions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "fault_plan_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("fault_injection_plans.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("fault_point", sa.String(length=160), nullable=False),
        sa.Column("scope_key", sa.String(length=240), nullable=False),
        sa.Column("category", sa.String(length=80), nullable=False),
        sa.Column("remaining_triggers", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_fault_consumption_plan_created",
        "fault_injection_consumptions",
        ["fault_plan_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_fault_consumption_plan_created", table_name="fault_injection_consumptions")
    op.drop_table("fault_injection_consumptions")
    op.drop_index("ix_fault_injection_plans_active", table_name="fault_injection_plans")
    op.drop_table("fault_injection_plans")
    postgresql.ENUM(name="fault_plan_status").drop(op.get_bind(), checkfirst=True)
