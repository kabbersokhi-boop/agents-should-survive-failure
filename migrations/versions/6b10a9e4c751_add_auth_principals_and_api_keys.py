"""add authentication principals and API keys

Revision ID: 6b10a9e4c751
Revises: 4d902f85f639
Create Date: 2026-07-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "6b10a9e4c751"
down_revision: str | None = "4d902f85f639"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    principal_type = sa.Enum("USER", "SERVICE", "AGENT", name="principal_type")
    principal_status = sa.Enum("ACTIVE", "DISABLED", name="principal_status")
    principal_type.create(op.get_bind(), checkfirst=True)
    principal_status.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "auth_principals",
        sa.Column("principal_type", principal_type, nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=False),
        sa.Column("status", principal_status, nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("agent_id", sa.Uuid(), nullable=True),
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
        sa.CheckConstraint(
            "(principal_type = 'USER' AND user_id IS NOT NULL AND agent_id IS NULL) OR "
            "(principal_type = 'AGENT' AND user_id IS NULL AND agent_id IS NOT NULL) OR "
            "(principal_type = 'SERVICE' AND user_id IS NULL AND agent_id IS NULL)",
            name="ck_auth_principal_identity",
        ),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("agent_id"),
        sa.UniqueConstraint("user_id"),
    )
    op.execute(
        "INSERT INTO auth_principals (id, principal_type, display_name, status, user_id) "
        "SELECT id, 'USER', display_name, "
        "CASE WHEN status = 'ACTIVE' THEN 'ACTIVE'::principal_status "
        "ELSE 'DISABLED'::principal_status END, id FROM users"
    )
    op.create_table(
        "api_keys",
        sa.Column("principal_id", sa.Uuid(), nullable=False),
        sa.Column("key_identifier", sa.String(length=32), nullable=False),
        sa.Column("key_prefix", sa.String(length=20), nullable=False),
        sa.Column("last_four", sa.String(length=4), nullable=False),
        sa.Column("secret_hash", sa.Text(), nullable=False),
        sa.Column("label", sa.String(length=120), nullable=False),
        sa.Column("scopes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.ForeignKeyConstraint(["principal_id"], ["auth_principals.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("key_identifier", name="uq_api_key_identifier"),
    )
    op.create_index("ix_api_keys_principal", "api_keys", ["principal_id"], unique=False)
    for table, column in (
        ("workflow_runs", "requested_by_id"),
        ("evaluation_runs", "requested_by_id"),
        ("approval_decisions", "decided_by_id"),
        ("audit_events", "actor_id"),
    ):
        op.drop_constraint(f"{table}_{column}_fkey", table, type_="foreignkey")
        op.create_foreign_key(
            f"{table}_{column}_auth_principal_fkey",
            table,
            "auth_principals",
            [column],
            ["id"],
            ondelete="RESTRICT" if table != "audit_events" else "SET NULL",
        )


def downgrade() -> None:
    for table, column in (
        ("audit_events", "actor_id"),
        ("approval_decisions", "decided_by_id"),
        ("evaluation_runs", "requested_by_id"),
        ("workflow_runs", "requested_by_id"),
    ):
        op.drop_constraint(f"{table}_{column}_auth_principal_fkey", table, type_="foreignkey")
        op.create_foreign_key(
            f"{table}_{column}_fkey",
            table,
            "users",
            [column],
            ["id"],
            ondelete="RESTRICT" if table != "audit_events" else "SET NULL",
        )
    op.drop_index("ix_api_keys_principal", table_name="api_keys")
    op.drop_table("api_keys")
    op.drop_table("auth_principals")
    sa.Enum(name="principal_status").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="principal_type").drop(op.get_bind(), checkfirst=True)
