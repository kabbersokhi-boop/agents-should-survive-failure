import asyncio
import json
import os
import subprocess
import uuid
from collections.abc import Awaitable, Callable
from typing import Any, cast

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agents_should_survive_failure.persistence.models import (
    ApprovalRequest,
    ApprovalStatus,
    ApprovedVendor,
    AuditEvent,
    RunStatus,
    SyntheticEmailMessage,
    ToolDefinition,
    ToolInvocation,
    Vendor,
    VendorStatus,
    WorkflowEvent,
    WorkflowRun,
)


def auth_headers() -> dict[str, str]:
    key = os.environ["INTEGRATION_API_KEY"]
    return {"Authorization": f"Bearer {key}"}


async def eventually[T](operation: Callable[[], Awaitable[T]], attempts: int = 45) -> T:
    last_error: BaseException | None = None
    for _ in range(attempts):
        try:
            return await operation()
        except (AssertionError, httpx.HTTPError) as error:
            last_error = error
            await asyncio.sleep(1)
    raise AssertionError("workflow did not reach expected state") from last_error


@pytest.mark.integration
@pytest.mark.asyncio
async def test_vendor_onboarding_survives_worker_restart_and_records_approval() -> None:
    reference = f"workflow-{uuid.uuid4()}"
    idempotency_key = f"onboarding-{uuid.uuid4()}"
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1:8000", timeout=15, headers=auth_headers()
    ) as client:
        created = await client.post(
            "/vendors",
            json={
                "external_reference": reference,
                "legal_name": "Durable Workflow Vendor",
                "jurisdiction": "US",
                "contact_email": "durable@example.invalid",
            },
        )
        created.raise_for_status()
        vendor_id = created.json()["id"]
        started = await client.post(
            f"/vendors/{vendor_id}/onboarding", json={"idempotency_key": idempotency_key}
        )
        started.raise_for_status()
        run_id = started.json()["id"]

        async def waiting_for_approval() -> None:
            response = await client.get(f"/workflow-runs/{run_id}")
            response.raise_for_status()
            assert response.json()["phase"] == "waiting_for_approval"

        await eventually(waiting_for_approval)

        approvals = await client.get(f"/api/v1/workflow-runs/{run_id}/approvals")
        approvals.raise_for_status()
        pending_approval = approvals.json()[0]

        await asyncio.to_thread(
            subprocess.run,
            ["docker", "compose", "restart", "worker"],
            check=True,
            env=os.environ.copy(),
        )
        decision = await client.post(
            f"/workflow-runs/{run_id}/approval",
            json={
                "approval_request_id": pending_approval["id"],
                "expected_version": pending_approval["version"],
                "decision": "approved",
                "rationale": "Synthetic vendor accepted after review.",
                "idempotency_key": f"decision-{uuid.uuid4()}",
            },
        )
        assert decision.status_code == 202

        async def workflow_completed() -> None:
            response = await client.get(f"/workflow-runs/{run_id}")
            response.raise_for_status()
            assert response.json()["phase"] == "completed"

        await eventually(workflow_completed)
        evidence_response = await client.get(f"/workflow-runs/{run_id}/evidence")
        evidence_response.raise_for_status()
        evidence = evidence_response.json()

    engine = create_async_engine(
        os.getenv(
            "INTEGRATION_DATABASE_URL",
            "postgresql+asyncpg://agents:local-development-only@127.0.0.1:5432/agents",
        )
    )
    try:
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as session:
            run = await session.scalar(select(WorkflowRun).where(WorkflowRun.id == run_id))
            vendor = await session.scalar(select(Vendor).where(Vendor.id == vendor_id))
            approval = await session.scalar(
                select(ApprovalRequest).where(ApprovalRequest.workflow_run_id == run_id)
            )
            events = (
                await session.scalars(
                    select(WorkflowEvent)
                    .where(WorkflowEvent.workflow_run_id == run_id)
                    .order_by(WorkflowEvent.sequence)
                )
            ).all()
            audit_events = (
                await session.scalars(
                    select(AuditEvent).where(AuditEvent.workflow_run_id == run_id)
                )
            ).all()
            approved_vendor = await session.scalar(
                select(ApprovedVendor).where(ApprovedVendor.workflow_run_id == run_id)
            )
            tool_names = (
                await session.scalars(
                    select(ToolDefinition.name)
                    .join(ToolInvocation, ToolInvocation.tool_definition_id == ToolDefinition.id)
                    .where(ToolInvocation.workflow_run_id == run_id)
                    .order_by(ToolInvocation.created_at)
                )
            ).all()
            synthetic_messages = (
                await session.scalars(
                    select(SyntheticEmailMessage).where(
                        SyntheticEmailMessage.workflow_run_id == run_id
                    )
                )
            ).all()
    finally:
        await engine.dispose()

    assert run is not None and run.status is RunStatus.SUCCEEDED
    assert vendor is not None and vendor.status is VendorStatus.APPROVED and vendor.risk_score == 25
    assert approval is not None and approval.status is ApprovalStatus.APPROVED
    assert approved_vendor is not None
    assert tool_names == [
        "vendor_database_query",
        "internal_policy_search",
        "synthetic_email_send",
    ]
    assert len(synthetic_messages) == 1
    assert synthetic_messages[0].status == "simulated"
    assert [event.event_type for event in events] == [
        "review.started",
        "risk.assessed",
        "risk.policy_context",
        "approval.requested",
        "approval.decided",
    ]
    assert events[2].payload["model_explanation_available"] is True
    assert events[2].payload["citations"]
    assert [event["event_type"] for event in evidence["events"]] == [
        "review.started",
        "risk.assessed",
        "risk.policy_context",
        "approval.requested",
        "approval.decided",
    ]
    assert evidence["events"][2]["payload"]["citations"]
    assert evidence["model_calls"][0]["status"] == "succeeded"
    assert "prompt" not in evidence["model_calls"][0]
    assert {event.action for event in audit_events} == {
        "vendor.review.start",
        "vendor.risk.assess",
        "approval.request.create",
        "approval.decision.record",
    }


@pytest.mark.integration
@pytest.mark.asyncio
async def test_vendor_onboarding_cancellation_is_durable() -> None:
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1:8000", timeout=15, headers=auth_headers()
    ) as client:
        created = await client.post(
            "/vendors",
            json={
                "external_reference": f"cancel-{uuid.uuid4()}",
                "legal_name": "Cancelled Workflow Vendor",
                "jurisdiction": "CA",
                "contact_email": "cancel@example.invalid",
            },
        )
        created.raise_for_status()
        vendor_id = created.json()["id"]
        started = await client.post(
            f"/vendors/{vendor_id}/onboarding", json={"idempotency_key": f"cancel-{uuid.uuid4()}"}
        )
        started.raise_for_status()
        run_id = started.json()["id"]

        async def waiting_for_approval() -> None:
            response = await client.get(f"/workflow-runs/{run_id}")
            response.raise_for_status()
            assert response.json()["phase"] == "waiting_for_approval"

        await eventually(waiting_for_approval)
        cancelled = await client.delete(f"/workflow-runs/{run_id}")
        assert cancelled.status_code == 202

        async def workflow_cancelled() -> None:
            response = await client.get(f"/workflow-runs/{run_id}")
            response.raise_for_status()
            assert response.json()["phase"] == "cancelled"

        await eventually(workflow_cancelled)

    engine = create_async_engine(
        os.getenv(
            "INTEGRATION_DATABASE_URL",
            "postgresql+asyncpg://agents:local-development-only@127.0.0.1:5432/agents",
        )
    )
    try:
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as session:
            run = await session.scalar(select(WorkflowRun).where(WorkflowRun.id == run_id))
            events = (
                await session.scalars(
                    select(WorkflowEvent)
                    .where(WorkflowEvent.workflow_run_id == run_id)
                    .order_by(WorkflowEvent.sequence)
                )
            ).all()
    finally:
        await engine.dispose()

    assert run is not None and run.status is RunStatus.CANCELLED
    assert [event.event_type for event in events][-1] == "review.cancelled"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_workflow_event_stream_replays_persisted_evidence() -> None:
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1:8000", timeout=15, headers=auth_headers()
    ) as client:
        created = await client.post(
            "/vendors",
            json={
                "external_reference": f"event-stream-{uuid.uuid4()}",
                "legal_name": "Event Stream Vendor",
                "jurisdiction": "US",
                "contact_email": "events@example.invalid",
            },
        )
        created.raise_for_status()
        vendor_id = created.json()["id"]
        started = await client.post(
            f"/vendors/{vendor_id}/onboarding", json={"idempotency_key": f"stream-{uuid.uuid4()}"}
        )
        started.raise_for_status()
        run_id = started.json()["id"]

        async def waiting_for_approval() -> None:
            response = await client.get(f"/workflow-runs/{run_id}")
            response.raise_for_status()
            assert response.json()["phase"] == "waiting_for_approval"

        await eventually(waiting_for_approval)

        async def approval_event_is_persisted() -> None:
            response = await client.get(f"/api/v1/workflow-runs/{run_id}/events")
            response.raise_for_status()
            assert any(event["event_type"] == "approval.requested" for event in response.json())

        await eventually(approval_event_is_persisted)
        received_event: dict[str, Any] | None = None
        async with client.stream(
            "GET", f"/api/v1/workflow-runs/{run_id}/events/stream?after_sequence=0"
        ) as response:
            response.raise_for_status()
            assert response.headers["content-type"].startswith("text/event-stream")
            async for line in response.aiter_lines():
                if line.startswith("data: "):
                    parsed_event = cast(dict[str, Any], json.loads(line.removeprefix("data: ")))
                    received_event = parsed_event
                    if parsed_event["event_type"] == "approval.requested":
                        break

        assert received_event is not None
        assert received_event["sequence"] == 30
        assert received_event["payload"]["risk_score"] == 25
        cancelled = await client.delete(f"/workflow-runs/{run_id}")
        assert cancelled.status_code == 202

        async def workflow_cancelled() -> None:
            response = await client.get(f"/workflow-runs/{run_id}")
            response.raise_for_status()
            assert response.json()["phase"] == "cancelled"

        await eventually(workflow_cancelled)
