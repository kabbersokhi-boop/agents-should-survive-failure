"""protect versioned agent and tool contracts

Revision ID: e8f1a2b3c4d5
Revises: c4d7e8f9a0b1
Create Date: 2026-07-15 04:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "e8f1a2b3c4d5"
down_revision: str | None = "c4d7e8f9a0b1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
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
    op.execute(
        """
        CREATE FUNCTION reject_tool_definition_contract_mutation()
        RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'tool definitions cannot be deleted';
            END IF;
            IF NEW.id IS DISTINCT FROM OLD.id
                OR NEW.name IS DISTINCT FROM OLD.name
                OR NEW.version IS DISTINCT FROM OLD.version
                OR NEW.description IS DISTINCT FROM OLD.description
                OR NEW.input_schema IS DISTINCT FROM OLD.input_schema
                OR NEW.output_schema IS DISTINCT FROM OLD.output_schema
                OR NEW.permissions IS DISTINCT FROM OLD.permissions
                OR NEW.risk_class IS DISTINCT FROM OLD.risk_class
                OR NEW.timeout_seconds IS DISTINCT FROM OLD.timeout_seconds
                OR NEW.approval_required IS DISTINCT FROM OLD.approval_required THEN
                RAISE EXCEPTION 'tool definition contract is immutable';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER tool_definitions_immutable_contract
        BEFORE UPDATE OR DELETE ON tool_definitions
        FOR EACH ROW EXECUTE FUNCTION reject_tool_definition_contract_mutation()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER tool_definitions_immutable_contract ON tool_definitions")
    op.execute("DROP FUNCTION reject_tool_definition_contract_mutation()")
    op.execute("DROP TRIGGER agents_immutable_contract ON agents")
    op.execute("DROP FUNCTION reject_agent_contract_mutation()")
