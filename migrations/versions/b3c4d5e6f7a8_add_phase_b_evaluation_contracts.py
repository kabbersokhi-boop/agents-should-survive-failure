"""add Phase B evaluation contracts

Revision ID: b3c4d5e6f7a8
Revises: f1a2b3c4d5e6
Create Date: 2026-07-16 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b3c4d5e6f7a8"
down_revision: str | None = "f1a2b3c4d5e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "evaluation_runs",
        sa.Column("suite_slug", sa.String(length=160), nullable=True),
    )
    op.add_column(
        "evaluation_runs",
        sa.Column("suite_version", sa.String(length=40), nullable=True),
    )
    op.add_column(
        "evaluation_runs",
        sa.Column("suite_schema_version", sa.String(length=40), nullable=True),
    )
    op.add_column(
        "evaluation_runs",
        sa.Column("dataset_sha256", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "evaluation_runs",
        sa.Column("request_fingerprint", sa.String(length=64), nullable=True),
    )
    op.execute(
        "UPDATE evaluation_runs SET suite_slug = 'legacy-static-evaluation', "
        "suite_version = '0', suite_schema_version = '0', "
        "dataset_sha256 = repeat('0', 64), request_fingerprint = repeat('0', 64)"
    )
    op.alter_column("evaluation_runs", "suite_slug", nullable=False)
    op.alter_column("evaluation_runs", "suite_version", nullable=False)
    op.alter_column("evaluation_runs", "suite_schema_version", nullable=False)
    op.alter_column("evaluation_runs", "dataset_sha256", nullable=False)
    op.alter_column("evaluation_runs", "request_fingerprint", nullable=False)
    op.create_check_constraint(
        "ck_evaluation_run_dataset_sha256",
        "evaluation_runs",
        "dataset_sha256 ~ '^[0-9a-f]{64}$'",
    )
    op.create_check_constraint(
        "ck_evaluation_run_request_fingerprint",
        "evaluation_runs",
        "request_fingerprint ~ '^[0-9a-f]{64}$'",
    )
    op.drop_constraint("uq_evaluation_run_key", "evaluation_runs", type_="unique")
    op.create_unique_constraint(
        "uq_evaluation_run_principal_idempotency_key",
        "evaluation_runs",
        ["requested_by_id", "idempotency_key"],
    )

    op.add_column(
        "evaluation_cases",
        sa.Column("suite_slug", sa.String(length=160), nullable=True),
    )
    op.add_column(
        "evaluation_cases",
        sa.Column("suite_version", sa.String(length=40), nullable=True),
    )
    op.add_column(
        "evaluation_cases",
        sa.Column("schema_version", sa.String(length=40), nullable=True),
    )
    op.add_column(
        "evaluation_cases",
        sa.Column("title", sa.String(length=200), nullable=True),
    )
    op.add_column("evaluation_cases", sa.Column("description", sa.Text(), nullable=True))
    op.add_column(
        "evaluation_cases",
        sa.Column("scenario_type", sa.String(length=120), nullable=True),
    )
    op.add_column(
        "evaluation_cases",
        sa.Column(
            "setup",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.add_column(
        "evaluation_cases",
        sa.Column(
            "driver",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.add_column(
        "evaluation_cases",
        sa.Column(
            "evidence_requirements",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.add_column(
        "evaluation_cases",
        sa.Column("content_sha256", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "evaluation_cases",
        sa.Column("reviewed_by", sa.String(length=160), nullable=True),
    )
    op.add_column(
        "evaluation_cases",
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute(
        "UPDATE evaluation_cases SET "
        "suite_slug = 'legacy-static-evaluation', suite_version = version, "
        "schema_version = '0', title = slug, "
        "description = 'Legacy pre-Phase-B static evaluation case.', "
        "scenario_type = 'legacy_static', setup = '{}'::jsonb, driver = '{}'::jsonb, "
        "evidence_requirements = '[]'::jsonb, content_sha256 = repeat('0', 64), "
        "reviewed_by = 'legacy migration', reviewed_at = created_at"
    )
    for column in (
        "suite_slug",
        "suite_version",
        "schema_version",
        "title",
        "description",
        "scenario_type",
        "setup",
        "driver",
        "evidence_requirements",
        "content_sha256",
        "reviewed_by",
        "reviewed_at",
    ):
        op.alter_column("evaluation_cases", column, nullable=False)
    op.create_check_constraint(
        "ck_evaluation_case_content_sha256",
        "evaluation_cases",
        "content_sha256 ~ '^[0-9a-f]{64}$'",
    )
    op.drop_constraint("uq_evaluation_case_version", "evaluation_cases", type_="unique")
    op.create_unique_constraint(
        "uq_evaluation_case_suite_slug",
        "evaluation_cases",
        ["suite_slug", "suite_version", "slug"],
    )
    op.create_index(
        op.f("ix_evaluation_cases_scenario_type"),
        "evaluation_cases",
        ["scenario_type"],
        unique=False,
    )
    op.execute(
        """
        CREATE FUNCTION reject_evaluation_case_contract_mutation()
        RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'reviewed evaluation cases cannot be deleted';
            END IF;
            IF NEW.id IS DISTINCT FROM OLD.id
                OR NEW.suite_slug IS DISTINCT FROM OLD.suite_slug
                OR NEW.suite_version IS DISTINCT FROM OLD.suite_version
                OR NEW.schema_version IS DISTINCT FROM OLD.schema_version
                OR NEW.slug IS DISTINCT FROM OLD.slug
                OR NEW.version IS DISTINCT FROM OLD.version
                OR NEW.workflow_type IS DISTINCT FROM OLD.workflow_type
                OR NEW.title IS DISTINCT FROM OLD.title
                OR NEW.description IS DISTINCT FROM OLD.description
                OR NEW.scenario_type IS DISTINCT FROM OLD.scenario_type
                OR NEW.input_data IS DISTINCT FROM OLD.input_data
                OR NEW.setup IS DISTINCT FROM OLD.setup
                OR NEW.driver IS DISTINCT FROM OLD.driver
                OR NEW.expected_outcome IS DISTINCT FROM OLD.expected_outcome
                OR NEW.evidence_requirements IS DISTINCT FROM OLD.evidence_requirements
                OR NEW.content_sha256 IS DISTINCT FROM OLD.content_sha256
                OR NEW.reviewed_by IS DISTINCT FROM OLD.reviewed_by
                OR NEW.reviewed_at IS DISTINCT FROM OLD.reviewed_at
                OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
                RAISE EXCEPTION 'reviewed evaluation case contract is immutable';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER evaluation_cases_immutable_contract
        BEFORE UPDATE OR DELETE ON evaluation_cases
        FOR EACH ROW EXECUTE FUNCTION reject_evaluation_case_contract_mutation()
        """
    )

    op.add_column(
        "evaluation_results",
        sa.Column("case_slug", sa.String(length=160), nullable=True),
    )
    op.add_column(
        "evaluation_results",
        sa.Column("case_version", sa.String(length=40), nullable=True),
    )
    op.add_column(
        "evaluation_results",
        sa.Column("case_content_sha256", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "evaluation_results",
        sa.Column(
            "expected_outcome",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.add_column(
        "evaluation_results",
        sa.Column(
            "actual_outcome",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.add_column(
        "evaluation_results",
        sa.Column("failure_category", sa.String(length=120), nullable=True),
    )
    op.add_column(
        "evaluation_results",
        sa.Column("duration_ms", sa.Integer(), nullable=True),
    )
    op.add_column(
        "evaluation_results",
        sa.Column(
            "evidence_summary",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.execute(
        "UPDATE evaluation_results AS result SET "
        "case_slug = case_row.slug, case_version = case_row.version, "
        "case_content_sha256 = case_row.content_sha256, "
        "expected_outcome = case_row.expected_outcome "
        "FROM evaluation_cases AS case_row "
        "WHERE case_row.id = result.evaluation_case_id"
    )
    op.alter_column("evaluation_results", "case_slug", nullable=False)
    op.alter_column("evaluation_results", "case_version", nullable=False)
    op.alter_column("evaluation_results", "case_content_sha256", nullable=False)
    op.alter_column("evaluation_results", "expected_outcome", nullable=False)
    op.create_check_constraint(
        "ck_evaluation_result_duration",
        "evaluation_results",
        "duration_ms IS NULL OR duration_ms >= 0",
    )
    op.create_check_constraint(
        "ck_evaluation_result_case_content_sha256",
        "evaluation_results",
        "case_content_sha256 ~ '^[0-9a-f]{64}$'",
    )
    op.execute(
        """
        CREATE FUNCTION reject_evaluation_result_mutation()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'evaluation result snapshots cannot be updated or deleted';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER evaluation_results_immutable_snapshot
        BEFORE UPDATE OR DELETE ON evaluation_results
        FOR EACH ROW EXECUTE FUNCTION reject_evaluation_result_mutation()
        """
    )


def downgrade() -> None:
    # Phase A cannot represent reviewed B1 provenance. Remove only B1 evaluation artifacts before
    # flattening the schema so a later re-upgrade can safely reseed the immutable reviewed catalog.
    # Legacy pre-B1 rows and runs remain intact.
    op.execute("DROP TRIGGER evaluation_results_immutable_snapshot ON evaluation_results")
    op.execute("DROP FUNCTION reject_evaluation_result_mutation()")
    op.execute("DROP TRIGGER evaluation_cases_immutable_contract ON evaluation_cases")
    op.execute("DROP FUNCTION reject_evaluation_case_contract_mutation()")
    op.execute(
        """
        DELETE FROM evaluation_results
        WHERE evaluation_run_id IN (
            SELECT id FROM evaluation_runs WHERE suite_slug <> 'legacy-static-evaluation'
        ) OR evaluation_case_id IN (
            SELECT id FROM evaluation_cases WHERE suite_slug <> 'legacy-static-evaluation'
        )
        """
    )
    op.execute("DELETE FROM evaluation_runs WHERE suite_slug <> 'legacy-static-evaluation'")
    op.execute("DELETE FROM evaluation_cases WHERE suite_slug <> 'legacy-static-evaluation'")

    op.drop_constraint(
        "ck_evaluation_result_case_content_sha256",
        "evaluation_results",
        type_="check",
    )
    op.drop_constraint("ck_evaluation_result_duration", "evaluation_results", type_="check")
    op.drop_column("evaluation_results", "evidence_summary")
    op.drop_column("evaluation_results", "duration_ms")
    op.drop_column("evaluation_results", "failure_category")
    op.drop_column("evaluation_results", "actual_outcome")
    op.drop_column("evaluation_results", "expected_outcome")
    op.drop_column("evaluation_results", "case_content_sha256")
    op.drop_column("evaluation_results", "case_version")
    op.drop_column("evaluation_results", "case_slug")

    op.drop_index(op.f("ix_evaluation_cases_scenario_type"), table_name="evaluation_cases")
    op.drop_constraint("uq_evaluation_case_suite_slug", "evaluation_cases", type_="unique")
    op.drop_constraint("ck_evaluation_case_content_sha256", "evaluation_cases", type_="check")
    op.create_unique_constraint(
        "uq_evaluation_case_version", "evaluation_cases", ["slug", "version"]
    )
    op.drop_column("evaluation_cases", "reviewed_at")
    op.drop_column("evaluation_cases", "reviewed_by")
    op.drop_column("evaluation_cases", "content_sha256")
    op.drop_column("evaluation_cases", "evidence_requirements")
    op.drop_column("evaluation_cases", "driver")
    op.drop_column("evaluation_cases", "setup")
    op.drop_column("evaluation_cases", "scenario_type")
    op.drop_column("evaluation_cases", "description")
    op.drop_column("evaluation_cases", "title")
    op.drop_column("evaluation_cases", "schema_version")
    op.drop_column("evaluation_cases", "suite_version")
    op.drop_column("evaluation_cases", "suite_slug")

    op.drop_constraint("ck_evaluation_run_dataset_sha256", "evaluation_runs", type_="check")
    op.drop_constraint("ck_evaluation_run_request_fingerprint", "evaluation_runs", type_="check")
    op.drop_constraint(
        "uq_evaluation_run_principal_idempotency_key", "evaluation_runs", type_="unique"
    )
    op.create_unique_constraint("uq_evaluation_run_key", "evaluation_runs", ["idempotency_key"])
    op.drop_column("evaluation_runs", "request_fingerprint")
    op.drop_column("evaluation_runs", "dataset_sha256")
    op.drop_column("evaluation_runs", "suite_schema_version")
    op.drop_column("evaluation_runs", "suite_version")
    op.drop_column("evaluation_runs", "suite_slug")
