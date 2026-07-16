"""add run delegation root index

Revision ID: b5c6d7e8f9a0
Revises: a4b5c6d7e8f9
"""

from collections.abc import Sequence

from alembic import op

revision: str = "b5c6d7e8f9a0"
down_revision: str | None = "a4b5c6d7e8f9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_run_delegations_root_workflow_run_id",
        "run_delegations",
        ["root_workflow_run_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_run_delegations_root_workflow_run_id", table_name="run_delegations")
