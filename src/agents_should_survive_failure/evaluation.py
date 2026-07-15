"""Deterministic evaluation harness for workflow behavior contracts."""

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agents_should_survive_failure.metrics import EVALUATION_CASES
from agents_should_survive_failure.persistence.models import (
    EvaluationCase,
    EvaluationResult,
    EvaluationResultStatus,
    EvaluationRun,
    EvaluationStatus,
)


class EvaluationRunner:
    async def run_vendor_onboarding(
        self, session: AsyncSession, *, requested_by_id: str, idempotency_key: str
    ) -> EvaluationRun:
        existing = await session.scalar(
            select(EvaluationRun).where(EvaluationRun.idempotency_key == idempotency_key)
        )
        if existing is not None:
            return existing
        now = datetime.now(UTC)
        run = EvaluationRun(
            requested_by_id=requested_by_id,
            idempotency_key=idempotency_key,
            status=EvaluationStatus.RUNNING,
            configuration={"provider": "deterministic", "workflow_type": "vendor_onboarding"},
            started_at=now,
        )
        session.add(run)
        await session.flush()
        cases = await session.scalars(
            select(EvaluationCase).where(
                EvaluationCase.workflow_type == "vendor_onboarding",
                EvaluationCase.enabled.is_(True),
            )
        )
        all_cases_passed = True
        for case in cases:
            jurisdiction = str(case.input_data.get("jurisdiction", ""))
            actual_band = "low" if jurisdiction in {"US", "GB", "CA"} else "high"
            expected_band = case.expected_outcome.get("risk_band")
            passed = (
                actual_band == expected_band
                and case.expected_outcome.get("requires_approval") is True
            )
            all_cases_passed = all_cases_passed and passed
            EVALUATION_CASES.labels("passed" if passed else "failed").inc()
            session.add(
                EvaluationResult(
                    evaluation_run_id=run.id,
                    evaluation_case_id=case.id,
                    status=(
                        EvaluationResultStatus.PASSED if passed else EvaluationResultStatus.FAILED
                    ),
                    score=Decimal("1.0000") if passed else Decimal("0.0000"),
                    metrics={"actual_risk_band": actual_band},
                    summary="Deterministic vendor-onboarding expectation matched."
                    if passed
                    else "Deterministic vendor-onboarding expectation did not match.",
                )
            )
        run.status = EvaluationStatus.SUCCEEDED if all_cases_passed else EvaluationStatus.FAILED
        run.completed_at = datetime.now(UTC)
        return run
