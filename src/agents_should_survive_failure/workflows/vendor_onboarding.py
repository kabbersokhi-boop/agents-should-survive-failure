"""Temporal workflow for durable, human-approved vendor onboarding."""

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from agents_should_survive_failure.workflows.contracts import (
        ApprovalDecisionInput,
        ApprovalDecisionType,
        RiskAssessment,
        VendorOnboardingInput,
        WorkflowStatus,
    )


ACTIVITY_RETRY = RetryPolicy(initial_interval=timedelta(seconds=1), maximum_attempts=3)


@workflow.defn
class VendorOnboardingWorkflow:
    def __init__(self) -> None:
        self._phase = "created"
        self._approval_request_id: str | None = None
        self._decision: ApprovalDecisionInput | None = None
        self._cancelled = False

    @workflow.run
    async def run(self, input: VendorOnboardingInput) -> ApprovalDecisionType | None:
        await workflow.execute_activity(
            "vendor_onboarding.begin_review",
            input,
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=ACTIVITY_RETRY,
        )
        self._phase = "assessing_risk"
        assessment: RiskAssessment = await workflow.execute_activity(
            "vendor_onboarding.assess_risk",
            input,
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=ACTIVITY_RETRY,
        )
        self._phase = "waiting_for_approval"
        self._approval_request_id = await workflow.execute_activity(
            "vendor_onboarding.request_approval",
            args=[input, assessment],
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=ACTIVITY_RETRY,
        )
        await workflow.wait_condition(lambda: self._decision is not None or self._cancelled)
        if self._cancelled:
            await workflow.execute_activity(
                "vendor_onboarding.cancel_review",
                input,
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=ACTIVITY_RETRY,
            )
            self._phase = "cancelled"
            return None
        assert self._decision is not None
        self._phase = "recording_decision"
        await workflow.execute_activity(
            "vendor_onboarding.record_decision",
            args=[input, self._decision],
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=ACTIVITY_RETRY,
        )
        self._phase = "completed"
        return self._decision.decision

    @workflow.update
    def decide(self, decision: ApprovalDecisionInput) -> None:
        self._decision = decision

    @decide.validator
    def validate_decision(self, decision: ApprovalDecisionInput) -> None:
        """Reject invalid approval updates before they mutate workflow state."""
        if self._phase != "waiting_for_approval":
            raise ValueError("workflow is not waiting for an approval decision")
        if self._approval_request_id != decision.approval_request_id:
            raise ValueError("approval request does not belong to this workflow state")
        if self._cancelled:
            raise ValueError("workflow has been cancelled")
        if self._decision is not None:
            if self._decision == decision:
                return
            raise ValueError("workflow already has an approval decision")

    @workflow.signal
    def cancel(self) -> None:
        if self._decision is None:
            self._cancelled = True

    @workflow.query
    def status(self) -> WorkflowStatus:
        return WorkflowStatus(
            phase=self._phase,
            approval_request_id=self._approval_request_id,
            decision=self._decision.decision if self._decision else None,
        )
