"""Durable, human-authorized high-value refund workflow."""

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from agents_should_survive_failure.workflows.contracts import (
        ApprovalDecisionInput,
        ApprovalDecisionType,
        RefundWorkflowInput,
    )

RETRY = RetryPolicy(initial_interval=timedelta(seconds=1), maximum_attempts=3)


@workflow.defn
class RefundWorkflow:
    def __init__(self) -> None:
        self._approval_request_id: str | None = None
        self._decision: ApprovalDecisionInput | None = None

    @workflow.run
    async def run(self, input: RefundWorkflowInput) -> str | None:
        order = await workflow.execute_activity(
            "refund.retrieve_order_evidence",
            input,
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=RETRY,
        )
        policy = await workflow.execute_activity(
            "refund.retrieve_policy_evidence",
            input,
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=RETRY,
        )
        risk = await workflow.execute_activity(
            "refund.calculate_refund_risk",
            args=[input, order, policy],
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=RETRY,
        )
        explanation = await workflow.execute_activity(
            "refund.explain_refund_risk",
            args=[input, risk, policy],
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=RETRY,
        )
        self._approval_request_id = await workflow.execute_activity(
            "refund.request_approval",
            args=[input, risk, explanation],
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=RETRY,
        )
        await workflow.wait_condition(lambda: self._decision is not None)
        assert self._decision is not None
        await workflow.execute_activity(
            "refund.commit_refund_decision",
            args=[input, risk, explanation, self._decision],
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=RETRY,
        )
        if self._decision.decision is ApprovalDecisionType.APPROVED:
            await workflow.execute_activity(
                "refund.send_refund_notification",
                input,
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=RETRY,
            )
        return self._decision.decision.value

    @workflow.update
    def decide(self, decision: ApprovalDecisionInput) -> None:
        self._decision = decision

    @decide.validator
    def validate_decision(self, decision: ApprovalDecisionInput) -> None:
        if (
            self._approval_request_id != decision.approval_request_id
            or self._decision is not None
            or decision.expected_version != 1
        ):
            raise ValueError("approval decision is stale or not for this refund")
