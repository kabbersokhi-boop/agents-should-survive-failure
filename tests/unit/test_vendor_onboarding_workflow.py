from collections.abc import Callable

import pytest

from agents_should_survive_failure.workflows import vendor_onboarding
from agents_should_survive_failure.workflows.contracts import (
    ApprovalDecisionInput,
    ApprovalDecisionType,
    RiskAssessment,
    VendorOnboardingInput,
)


@pytest.mark.asyncio
async def test_workflow_records_approved_signal_after_durable_pause(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    async def execute_activity(activity: str, *args: object, **kwargs: object) -> object:
        calls.append(activity)
        if activity == "vendor_onboarding.assess_risk":
            return RiskAssessment(score=25, summary="low risk")
        if activity == "vendor_onboarding.request_approval":
            return "approval-1"
        return None

    async def wait_condition(predicate: Callable[[], bool]) -> None:
        assert predicate()

    monkeypatch.setattr(vendor_onboarding.workflow, "execute_activity", execute_activity)
    monkeypatch.setattr(vendor_onboarding.workflow, "wait_condition", wait_condition)
    instance = vendor_onboarding.VendorOnboardingWorkflow()
    instance.decide(
        ApprovalDecisionInput(
            decision=ApprovalDecisionType.APPROVED,
            decided_by_id="00000000-0000-0000-0000-000000000001",
            rationale="approved",
            idempotency_key="decision-1",
        )
    )

    result = await instance.run(VendorOnboardingInput(run_id="run-1", vendor_id="vendor-1"))

    assert result is ApprovalDecisionType.APPROVED
    assert calls == [
        "vendor_onboarding.begin_review",
        "vendor_onboarding.assess_risk",
        "vendor_onboarding.request_approval",
        "vendor_onboarding.record_decision",
    ]
    assert instance.status().phase == "completed"
    assert instance.status().approval_request_id == "approval-1"


@pytest.mark.asyncio
async def test_workflow_cancels_after_approval_pause(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    async def execute_activity(activity: str, *args: object, **kwargs: object) -> object:
        calls.append(activity)
        if activity == "vendor_onboarding.assess_risk":
            return RiskAssessment(score=65, summary="high risk")
        if activity == "vendor_onboarding.request_approval":
            return "approval-2"
        return None

    async def wait_condition(predicate: Callable[[], bool]) -> None:
        assert predicate()

    monkeypatch.setattr(vendor_onboarding.workflow, "execute_activity", execute_activity)
    monkeypatch.setattr(vendor_onboarding.workflow, "wait_condition", wait_condition)
    instance = vendor_onboarding.VendorOnboardingWorkflow()
    instance.cancel()

    result = await instance.run(VendorOnboardingInput(run_id="run-2", vendor_id="vendor-2"))

    assert result is None
    assert calls[-1] == "vendor_onboarding.cancel_review"
    assert instance.status().phase == "cancelled"


def test_workflow_ignores_decision_after_cancellation() -> None:
    instance = vendor_onboarding.VendorOnboardingWorkflow()
    instance.cancel()
    instance.decide(
        ApprovalDecisionInput(
            decision=ApprovalDecisionType.REJECTED,
            decided_by_id="00000000-0000-0000-0000-000000000001",
            rationale="rejected",
            idempotency_key="decision-2",
        )
    )

    assert instance.status().decision is None
