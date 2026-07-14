"""Workflow-level approval tests using Temporal's official time-skipping environment."""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import uuid4

import pytest
from temporalio import activity
from temporalio.client import WorkflowHandle
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from agents_should_survive_failure.workflows.contracts import (
    ApprovalDecisionInput,
    ApprovalDecisionType,
    RiskAssessment,
    VendorOnboardingInput,
    WorkflowStatus,
)
from agents_should_survive_failure.workflows.vendor_onboarding import VendorOnboardingWorkflow


@activity.defn(name="vendor_onboarding.begin_review")
async def begin_review(_: VendorOnboardingInput) -> None:
    return None


@activity.defn(name="vendor_onboarding.assess_risk")
async def assess_risk(_: VendorOnboardingInput) -> RiskAssessment:
    return RiskAssessment(score=25, summary="deterministic low risk")


@activity.defn(name="vendor_onboarding.request_approval")
async def request_approval(_: VendorOnboardingInput, __: RiskAssessment) -> str:
    return "temporal-approval-1"


@activity.defn(name="vendor_onboarding.record_decision")
async def record_decision(_: VendorOnboardingInput, __: ApprovalDecisionInput) -> None:
    return None


@activity.defn(name="vendor_onboarding.cancel_review")
async def cancel_review(_: VendorOnboardingInput) -> None:
    return None


async def wait_for_phase(
    handle: WorkflowHandle[Any, Any],
    expected_phase: str,
    *,
    attempts: int = 40,
) -> WorkflowStatus:
    last_status: WorkflowStatus | None = None
    for _ in range(attempts):
        status = await handle.query(VendorOnboardingWorkflow.status)
        last_status = status
        if status.phase == expected_phase:
            return status
        await asyncio.sleep(0.05)
    raise AssertionError(f"workflow did not reach {expected_phase}; last status was {last_status}")


@pytest.mark.asyncio
async def test_temporal_update_resolves_a_real_durable_approval_wait() -> None:
    """A workflow Update is validated and resumes the real wait condition exactly once."""
    task_queue = f"approval-test-{uuid4()}"
    activities: list[Callable[..., Awaitable[object]]] = [
        begin_review,
        assess_risk,
        request_approval,
        record_decision,
        cancel_review,
    ]
    async with (
        await WorkflowEnvironment.start_time_skipping() as environment,
        Worker(
            environment.client,
            task_queue=task_queue,
            workflows=[VendorOnboardingWorkflow],
            activities=activities,
        ),
    ):
        handle = await environment.client.start_workflow(
            VendorOnboardingWorkflow.run,
            VendorOnboardingInput(run_id="run-1", vendor_id="vendor-1"),
            id=f"approval-workflow-{uuid4()}",
            task_queue=task_queue,
        )
        status = await wait_for_phase(handle, "waiting_for_approval")
        assert status.approval_request_id == "temporal-approval-1"

        await handle.execute_update(  # pyright: ignore[reportUnknownMemberType]
            "decide",
            ApprovalDecisionInput(
                approval_request_id="temporal-approval-1",
                expected_version=1,
                decision=ApprovalDecisionType.APPROVED,
                decided_by_id="00000000-0000-0000-0000-000000000001",
                rationale="approved by the test principal",
                idempotency_key="test-approval-update",
            ),
            id="test-approval-update",
        )

        assert await handle.result() is ApprovalDecisionType.APPROVED
