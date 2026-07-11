"""scope workflow idempotency and add start leases

Revision ID: a81879aa36e9
Revises: 7f21c6d9a803
Create Date: 2026-07-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a81879aa36e9"
down_revision: str | None = "7f21c6d9a803"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("uq_workflow_run_idempotency_key", "workflow_runs", type_="unique")
    op.create_unique_constraint(
        "uq_workflow_run_principal_idempotency_key",
        "workflow_runs",
        ["requested_by_id", "idempotency_key"],
    )
    op.add_column("workflow_start_attempts", sa.Column("attempt_token", sa.Uuid(), nullable=True))
    op.add_column(
        "workflow_start_attempts",
        sa.Column("last_attempted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "workflow_start_attempts",
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("workflow_start_attempts", "lease_expires_at")
    op.drop_column("workflow_start_attempts", "last_attempted_at")
    op.drop_column("workflow_start_attempts", "attempt_token")
    op.drop_constraint("uq_workflow_run_principal_idempotency_key", "workflow_runs", type_="unique")
    op.create_unique_constraint(
        "uq_workflow_run_idempotency_key", "workflow_runs", ["idempotency_key"]
    )
