"""Phase B evaluation catalog validation and transitional persistence integrity runner."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import TypedDict
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from agents_should_survive_failure.evaluation_scenarios import (
    EvaluationCaseDefinition,
    EvaluationSuiteDefinition,
    load_packaged_evaluation_suite,
)
from agents_should_survive_failure.metrics import EVALUATION_CASES
from agents_should_survive_failure.persistence.models import (
    EvaluationCase,
    EvaluationResult,
    EvaluationResultStatus,
    EvaluationRun,
    EvaluationStatus,
)


class PersistedCaseInspection(TypedDict):
    valid: bool
    expected_case_sha256: str | None
    reconstructed_case_sha256: str | None
    mismatch_reasons: list[str]


class EvaluationRequestFingerprintConflict(Exception):
    """An evaluation idempotency key was reused for a different suite request."""


def evaluation_request_fingerprint(suite: EvaluationSuiteDefinition) -> str:
    """Hash the complete logical B1 request independently of caller and replay key."""

    payload = {
        "dataset_sha256": suite.content_sha256(),
        "suite_schema_version": suite.schema_version,
        "suite_slug": suite.suite_slug,
        "suite_version": suite.suite_version,
        "workflow_type": suite.workflow_type,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


class EvaluationRunner:
    """Run the explicitly limited B1 catalog-persistence integrity checks.

    This runner does not execute the production workflow and does not score business behavior.
    Phase B2 will replace it with real Temporal execution while preserving the reviewed suite
    contracts and immutable result snapshots introduced in B1.
    """

    async def run_vendor_onboarding(
        self, session: AsyncSession, *, requested_by_id: UUID, idempotency_key: str
    ) -> EvaluationRun:
        suite = load_packaged_evaluation_suite()
        dataset_sha256 = suite.content_sha256()
        request_fingerprint = evaluation_request_fingerprint(suite)
        definitions = {case.slug: case for case in suite.cases}
        now = datetime.now(UTC)
        run_id = uuid.uuid4()
        inserted = await session.execute(
            insert(EvaluationRun)
            .values(
                id=run_id,
                requested_by_id=requested_by_id,
                idempotency_key=idempotency_key,
                request_fingerprint=request_fingerprint,
                suite_slug=suite.suite_slug,
                suite_version=suite.suite_version,
                suite_schema_version=suite.schema_version,
                dataset_sha256=dataset_sha256,
                status=EvaluationStatus.RUNNING,
                configuration={
                    "workflow_type": suite.workflow_type,
                    "execution_mode": "b1_catalog_persistence_integrity",
                    "workflow_executed": False,
                    "dataset_case_count": len(suite.cases),
                    "catalog_case_count_expected": len(suite.cases),
                },
                started_at=now,
            )
            .on_conflict_do_nothing(constraint="uq_evaluation_run_principal_idempotency_key")
            .returning(EvaluationRun.id)
        )
        inserted_run_id = inserted.scalar_one_or_none()
        if inserted_run_id is None:
            existing = await session.scalar(
                select(EvaluationRun).where(
                    EvaluationRun.requested_by_id == requested_by_id,
                    EvaluationRun.idempotency_key == idempotency_key,
                )
            )
            if existing is None:
                raise RuntimeError("evaluation idempotency conflict could not be reloaded")
            if existing.request_fingerprint != request_fingerprint:
                raise EvaluationRequestFingerprintConflict
            return existing
        run = await session.get(EvaluationRun, run_id)
        if run is None:
            raise RuntimeError("inserted evaluation run could not be reloaded")

        result = await session.scalars(
            select(EvaluationCase)
            .where(
                EvaluationCase.suite_slug == suite.suite_slug,
                EvaluationCase.suite_version == suite.suite_version,
                EvaluationCase.schema_version == suite.schema_version,
            )
            .order_by(EvaluationCase.slug)
        )
        cases = result.all()
        persisted_slugs = {case.slug for case in cases}
        expected_slugs = set(definitions)
        missing_slugs = sorted(expected_slugs - persisted_slugs)
        unexpected_slugs = sorted(persisted_slugs - expected_slugs)
        unexpected_enabled_slugs = sorted(
            case.slug for case in cases if case.slug not in definitions and case.enabled
        )
        unexpected_disabled_slugs = sorted(
            case.slug for case in cases if case.slug not in definitions and not case.enabled
        )
        disabled_expected_slugs = sorted(
            case.slug for case in cases if case.slug in definitions and not case.enabled
        )
        run.configuration = {
            **run.configuration,
            "catalog_case_count_found": len(cases),
            "missing_case_slugs": missing_slugs,
            "unexpected_case_slugs": unexpected_slugs,
            "unexpected_enabled_case_slugs": unexpected_enabled_slugs,
            "unexpected_disabled_case_slugs": unexpected_disabled_slugs,
            "disabled_expected_case_slugs": disabled_expected_slugs,
        }

        all_cases_passed = not (
            missing_slugs
            or unexpected_enabled_slugs
            or unexpected_disabled_slugs
            or disabled_expected_slugs
        )
        if missing_slugs:
            EVALUATION_CASES.labels("failed").inc(len(missing_slugs))

        for case in cases:
            definition = definitions.get(case.slug)
            inspection = inspect_persisted_case(suite, definition, case)
            if definition is not None and not case.enabled:
                inspection["mismatch_reasons"].append("expected_case_disabled")
            passed = inspection["valid"] is True
            all_cases_passed = all_cases_passed and passed
            EVALUATION_CASES.labels("passed" if passed else "failed").inc()

            expected_outcome = (
                definition.expected_outcome.model_dump(mode="json")
                if definition is not None
                else case.expected_outcome
            )
            actual_outcome = {
                "catalog_record_valid": passed,
                "workflow_executed": False,
                "execution_mode": "b1_catalog_persistence_integrity",
                "mismatch_reasons": inspection["mismatch_reasons"],
            }
            session.add(
                EvaluationResult(
                    evaluation_run_id=run.id,
                    evaluation_case_id=case.id,
                    case_slug=case.slug,
                    case_version=case.version,
                    case_content_sha256=case.content_sha256,
                    status=(
                        EvaluationResultStatus.PASSED if passed else EvaluationResultStatus.FAILED
                    ),
                    score=Decimal("1.0000") if passed else Decimal("0.0000"),
                    expected_outcome=expected_outcome,
                    actual_outcome=actual_outcome,
                    failure_category=None if passed else "catalog_persistence_mismatch",
                    duration_ms=0,
                    metrics={
                        "catalog_record_valid": passed,
                        "workflow_executed": False,
                    },
                    evidence_summary={
                        "dataset_sha256": dataset_sha256,
                        "expected_case_sha256": inspection["expected_case_sha256"],
                        "persisted_case_sha256": case.content_sha256,
                        "reconstructed_case_sha256": inspection["reconstructed_case_sha256"],
                        "mismatch_reasons": inspection["mismatch_reasons"],
                        "note": (
                            "B1 validates catalog persistence only; no Temporal workflow ran."
                        ),
                    },
                    summary=(
                        "B1 catalog persistence matched; no Temporal workflow was executed."
                        if passed
                        else (
                            "B1 catalog persistence did not match the packaged contract; "
                            "no Temporal workflow was executed."
                        )
                    ),
                )
            )

        run.status = EvaluationStatus.SUCCEEDED if all_cases_passed else EvaluationStatus.FAILED
        run.completed_at = datetime.now(UTC)
        return run


def inspect_persisted_case(
    suite: EvaluationSuiteDefinition,
    definition: EvaluationCaseDefinition | None,
    case: EvaluationCase,
) -> PersistedCaseInspection:
    """Compare a persisted row with the packaged contract without scoring business behavior."""

    mismatch_reasons: list[str] = []
    expected_hash = suite.case_content_sha256(definition) if definition is not None else None
    reconstructed_hash: str | None = None
    persisted_definition: EvaluationCaseDefinition | None = None

    try:
        persisted_definition = EvaluationCaseDefinition.model_validate(
            {
                "slug": case.slug,
                "case_version": case.version,
                "scenario_type": case.scenario_type,
                "title": case.title,
                "description": case.description,
                "input": case.input_data,
                "setup": case.setup,
                "driver": case.driver,
                "expected_outcome": case.expected_outcome,
                "evidence_requirements": case.evidence_requirements,
            }
        )
        reconstructed_hash = suite.case_content_sha256(persisted_definition)
    except (TypeError, ValueError):
        mismatch_reasons.append("persisted_contract_invalid")

    if definition is None:
        mismatch_reasons.append("unexpected_case_slug")
    elif persisted_definition != definition:
        mismatch_reasons.append("persisted_contract_differs")

    metadata_checks = {
        "suite_slug_mismatch": case.suite_slug != suite.suite_slug,
        "suite_version_mismatch": case.suite_version != suite.suite_version,
        "schema_version_mismatch": case.schema_version != suite.schema_version,
        "workflow_type_mismatch": case.workflow_type != suite.workflow_type,
        "reviewed_by_mismatch": case.reviewed_by != suite.reviewed_by,
        "reviewed_at_mismatch": case.reviewed_at != suite.reviewed_at,
        "stored_case_hash_mismatch": case.content_sha256 != expected_hash,
        "reconstructed_case_hash_mismatch": reconstructed_hash != expected_hash,
    }
    mismatch_reasons.extend(reason for reason, failed in metadata_checks.items() if failed)

    return {
        "valid": not mismatch_reasons,
        "expected_case_sha256": expected_hash,
        "reconstructed_case_sha256": reconstructed_hash,
        "mismatch_reasons": mismatch_reasons,
    }
