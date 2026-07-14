"""record unregistered tool attempts

Revision ID: f0b2c4d6e8a0
Revises: e4f6a2b1c9d8
Create Date: 2026-07-15 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f0b2c4d6e8a0"
down_revision: str | None = "e4f6a2b1c9d8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("tool_invocations", "tool_definition_id", nullable=True)
    op.add_column(
        "tool_invocations", sa.Column("requested_tool_name", sa.String(length=120), nullable=True)
    )
    op.add_column(
        "tool_invocations", sa.Column("requested_tool_version", sa.String(length=40), nullable=True)
    )
    op.execute(
        "UPDATE tool_invocations SET requested_tool_name = tool_definitions.name, "
        "requested_tool_version = tool_definitions.version FROM tool_definitions "
        "WHERE tool_invocations.tool_definition_id = tool_definitions.id"
    )
    op.alter_column("tool_invocations", "requested_tool_name", nullable=False)
    op.alter_column("tool_invocations", "requested_tool_version", nullable=False)


def downgrade() -> None:
    op.drop_column("tool_invocations", "requested_tool_version")
    op.drop_column("tool_invocations", "requested_tool_name")
    op.alter_column("tool_invocations", "tool_definition_id", nullable=False)
