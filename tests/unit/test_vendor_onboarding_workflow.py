from collections.abc import Callable
from typing import Any, cast

import pytest

from agents_should_survive_failure.workflows import vendor_onboarding
from agents_should_survive_failure.workflows.contracts import (
    ApprovalDecisionInput,
    ApprovalDecisionType,
    RiskAssessment,
    VendorOnboardingInput,
)


@pytest.mark.asyncio
async def test_workflow_records_approved_update_after_durable_pause(
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

    instance = vendor_onboarding.VendorOnboardingWorkflow()

    async def wait_condition(predicate: Callable[[], bool]) -> None:
        cast(Callable[[ApprovalDecisionInput], None], instance.decide)(
            ApprovalDecisionInput(
                approval_request_id="approval-1",
                expected_version=1,
                decision=ApprovalDecisionType.APPROVED,
                decided_by_id="00000000-0000-0000-0000-000000000001",
                rationale="approved",
                idempotency_key="decision-1",
            )
        )
        assert predicate()

    monkeypatch.setattr(vendor_onboarding.workflow, "execute_activity", execute_activity)
    monkeypatch.setattr(vendor_onboarding.workflow, "wait_condition", wait_condition)
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


def test_workflow_update_validator_rejects_early_and_cancelled_decisions() -> None:
    instance = vendor_onboarding.VendorOnboardingWorkflow()
    decision = ApprovalDecisionInput(
        approval_request_id="approval-3",
        expected_version=1,
        decision=ApprovalDecisionType.REJECTED,
        decided_by_id="00000000-0000-0000-0000-000000000001",
        rationale="rejected",
        idempotency_key="decision-2",
    )
    with pytest.raises(ValueError, match="not waiting"):
        instance.validate_decision(decision)

    workflow_for_test = cast(Any, instance)
    workflow_for_test._phase = "waiting_for_approval"
    workflow_for_test._approval_request_id = decision.approval_request_id
    instance.cancel()
    with pytest.raises(ValueError, match="cancelled"):
        instance.validate_decision(decision)


def test_workflow_update_validator_rejects_conflicting_second_decision() -> None:
    instance = vendor_onboarding.VendorOnboardingWorkflow()
    workflow_for_test = cast(Any, instance)
    workflow_for_test._phase = "waiting_for_approval"
    workflow_for_test._approval_request_id = "approval-4"
    first = ApprovalDecisionInput(
        approval_request_id="approval-4",
        expected_version=1,
        decision=ApprovalDecisionType.APPROVED,
        decided_by_id="00000000-0000-0000-0000-000000000001",
        rationale="approved",
        idempotency_key="decision-4",
    )
    instance.validate_decision(first)
    cast(Callable[[ApprovalDecisionInput], None], instance.decide)(first)
    instance.validate_decision(first)
    with pytest.raises(ValueError, match="already has"):
        instance.validate_decision(
            ApprovalDecisionInput(
                approval_request_id="approval-4",
                expected_version=1,
                decision=ApprovalDecisionType.REJECTED,
                decided_by_id="00000000-0000-0000-0000-000000000001",
                rationale="rejected",
                idempotency_key="decision-5",
            )
        )
