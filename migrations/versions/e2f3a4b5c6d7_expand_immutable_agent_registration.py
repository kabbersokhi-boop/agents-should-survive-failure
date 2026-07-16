"""expand immutable agent registration

Revision ID: e2f3a4b5c6d7
Revises: d1e2f3a4b5c6
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e2f3a4b5c6d7"
down_revision: str | None = "d1e2f3a4b5c6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_LEGACY_DIGEST = "0" * 64


def _create_agent_trigger() -> None:
    op.execute(
        """
        CREATE FUNCTION reject_agent_contract_mutation()
        RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'registered agent versions cannot be deleted';
            END IF;
            IF NEW.id IS DISTINCT FROM OLD.id
                OR NEW.name IS DISTINCT FROM OLD.name
                OR NEW.version IS DISTINCT FROM OLD.version
                OR NEW.workflow_type IS DISTINCT FROM OLD.workflow_type
                OR NEW.package_name IS DISTINCT FROM OLD.package_name
                OR NEW.entry_point IS DISTINCT FROM OLD.entry_point
                OR NEW.manifest IS DISTINCT FROM OLD.manifest
                OR NEW.input_schema IS DISTINCT FROM OLD.input_schema
                OR NEW.output_schema IS DISTINCT FROM OLD.output_schema
                OR NEW.compatibility IS DISTINCT FROM OLD.compatibility
                OR NEW.integrity_digest IS DISTINCT FROM OLD.integrity_digest
                OR NEW.configuration IS DISTINCT FROM OLD.configuration THEN
                RAISE EXCEPTION 'registered agent version contract is immutable';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER agents_immutable_contract
        BEFORE UPDATE OR DELETE ON agents
        FOR EACH ROW EXECUTE FUNCTION reject_agent_contract_mutation()
        """
    )


def upgrade() -> None:
    op.execute("DROP TRIGGER agents_immutable_contract ON agents")
    op.execute("DROP FUNCTION reject_agent_contract_mutation()")
    op.add_column("agents", sa.Column("package_name", sa.String(length=120), nullable=True))
    op.add_column("agents", sa.Column("entry_point", sa.String(length=240), nullable=True))
    op.add_column(
        "agents", sa.Column("manifest", postgresql.JSONB(astext_type=sa.Text()), nullable=True)
    )
    op.add_column(
        "agents", sa.Column("input_schema", postgresql.JSONB(astext_type=sa.Text()), nullable=True)
    )
    op.add_column(
        "agents", sa.Column("output_schema", postgresql.JSONB(astext_type=sa.Text()), nullable=True)
    )
    op.add_column("agents", sa.Column("compatibility", sa.String(length=120), nullable=True))
    op.add_column("agents", sa.Column("integrity_digest", sa.String(length=64), nullable=True))
    op.execute(
        "UPDATE agents SET package_name = 'legacy-agent', entry_point = 'legacy:unavailable', "
        "manifest = '{}'::jsonb, input_schema = '{}'::jsonb, output_schema = '{}'::jsonb, "
        "compatibility = 'legacy', integrity_digest = '" + _LEGACY_DIGEST + "' "
        "WHERE package_name IS NULL"
    )
    for column in (
        "package_name",
        "entry_point",
        "manifest",
        "input_schema",
        "output_schema",
        "compatibility",
        "integrity_digest",
    ):
        op.alter_column("agents", column, nullable=False)
    op.create_check_constraint(
        "ck_agents_integrity_digest", "agents", "integrity_digest ~ '^[0-9a-f]{64}$'"
    )
    _create_agent_trigger()


def downgrade() -> None:
    op.execute("DROP TRIGGER agents_immutable_contract ON agents")
    op.execute("DROP FUNCTION reject_agent_contract_mutation()")
    op.drop_constraint("ck_agents_integrity_digest", "agents", type_="check")
    for column in (
        "integrity_digest",
        "compatibility",
        "output_schema",
        "input_schema",
        "manifest",
        "entry_point",
        "package_name",
    ):
        op.drop_column("agents", column)
    op.execute(
        """
        CREATE FUNCTION reject_agent_contract_mutation()
        RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'registered agent versions cannot be deleted';
            END IF;
            IF NEW.id IS DISTINCT FROM OLD.id
                OR NEW.name IS DISTINCT FROM OLD.name
                OR NEW.version IS DISTINCT FROM OLD.version
                OR NEW.workflow_type IS DISTINCT FROM OLD.workflow_type
                OR NEW.configuration IS DISTINCT FROM OLD.configuration THEN
                RAISE EXCEPTION 'registered agent version contract is immutable';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER agents_immutable_contract
        BEFORE UPDATE OR DELETE ON agents
        FOR EACH ROW EXECUTE FUNCTION reject_agent_contract_mutation()
        """
    )
