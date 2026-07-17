"""Release proof: kill a Docker worker after durable effects commit, before its ack."""

from __future__ import annotations

import asyncio
import os
import subprocess
import time
import uuid
from typing import Any

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import create_async_engine

from agents_should_survive_failure.fault_injection import FaultAction, FaultInjector, FaultPoint
from agents_should_survive_failure.persistence.models import (
    ApprovalDecision,
    ApprovalRequest,
    ApprovedVendor,
    FaultInjectionConsumption,
    SyntheticEmailMessage,
    ToolInvocation,
    WorkflowEvent,
    WorkflowRun,
)
from agents_should_survive_failure.persistence.session import Database
from agents_should_survive_failure.settings import get_settings


async def _wait_for(predicate: Any, *, deadline_seconds: float = 45) -> Any:
    deadline = time.monotonic() + deadline_seconds
    while time.monotonic() < deadline:
        value = await predicate()
        if value:
            return value
        await asyncio.sleep(0.25)
    raise TimeoutError("timed out waiting for worker-crash proof evidence")


async def main() -> None:
    api_key = os.environ["INTEGRATION_API_KEY"]
    engine = create_async_engine(get_settings().database_url)
    database = Database(engine)
    headers = {"Authorization": f"Bearer {api_key}"}
    async with httpx.AsyncClient(base_url="http://127.0.0.1:8000", headers=headers) as client:
        vendor = (
            (
                await client.post(
                    "/api/v1/vendors",
                    json={
                        "external_reference": f"worker-crash-{uuid.uuid4().hex[:12]}",
                        "legal_name": "Worker Crash Proof Vendor",
                        "jurisdiction": "US",
                        "contact_email": "worker-crash@example.invalid",
                    },
                )
            )
            .raise_for_status()
            .json()
        )
        started = (
            (
                await client.post(
                    f"/api/v1/vendors/{vendor['id']}/onboarding",
                    json={"idempotency_key": f"worker-crash-{uuid.uuid4().hex}"},
                )
            )
            .raise_for_status()
            .json()
        )
        run_id = started["id"]

        async def approval() -> dict[str, Any] | None:
            response = await client.get("/api/v1/approvals", params={"workflow_run_id": run_id})
            response.raise_for_status()
            return response.json()["items"][0] if response.json()["items"] else None

        pending = await _wait_for(approval)
        injector = FaultInjector(database, enabled=True)
        await injector.create(
            fault_point=FaultPoint.EMAIL_POST_COMMIT_HANDOFF,
            action=FaultAction.DELAY,
            scope_key=run_id,
            delay_ms=60_000,
            safe_metadata={"proof": "os-worker-kill"},
        )
        (
            await client.post(
                f"/api/v1/workflow-runs/{run_id}/approval",
                json={
                    "approval_request_id": pending["id"],
                    "expected_version": pending["version"],
                    "decision": "approved",
                    "rationale": "Release worker-crash proof.",
                    "idempotency_key": f"worker-crash-approval-{uuid.uuid4().hex}",
                },
            )
        ).raise_for_status()

        async def effects_committed() -> bool:
            async with database.session() as session:
                counts = [
                    await session.scalar(
                        select(func.count())
                        .select_from(ApprovalDecision)
                        .join(ApprovalRequest)
                        .where(ApprovalRequest.workflow_run_id == uuid.UUID(run_id))
                    ),
                    await session.scalar(
                        select(func.count())
                        .select_from(ApprovedVendor)
                        .where(ApprovedVendor.workflow_run_id == run_id)
                    ),
                    await session.scalar(
                        select(func.count())
                        .select_from(SyntheticEmailMessage)
                        .where(SyntheticEmailMessage.workflow_run_id == run_id)
                    ),
                    await session.scalar(
                        select(func.count())
                        .select_from(FaultInjectionConsumption)
                        .where(FaultInjectionConsumption.scope_key == run_id)
                    ),
                ]
            return counts == [1, 1, 1, 1]

        await _wait_for(effects_committed)
        delay_started = time.monotonic()
        worker_container = (
            await asyncio.to_thread(
                subprocess.check_output,
                ["docker", "compose", "ps", "-q", "worker"],
                text=True,
            )
        ).strip()
        assert worker_container
        before_pid = (
            await asyncio.to_thread(
                subprocess.check_output,
                ["docker", "inspect", "-f", "{{.State.Pid}}", worker_container],
                text=True,
            )
        ).strip()
        assert before_pid != "0"
        await asyncio.to_thread(
            subprocess.run, ["docker", "compose", "kill", "-s", "KILL", "worker"], check=True
        )
        assert time.monotonic() - delay_started < 5
        await asyncio.to_thread(
            subprocess.run, ["docker", "compose", "up", "-d", "worker"], check=True
        )

        async def replacement_process() -> tuple[str, str] | None:
            replacement_container = (
                await asyncio.to_thread(
                    subprocess.check_output,
                    ["docker", "compose", "ps", "-q", "worker"],
                    text=True,
                )
            ).strip()
            if not replacement_container or replacement_container == worker_container:
                return None
            try:
                replacement_pid = (
                    await asyncio.to_thread(
                        subprocess.check_output,
                        ["docker", "inspect", "-f", "{{.State.Pid}}", replacement_container],
                        text=True,
                    )
                ).strip()
            except subprocess.CalledProcessError:
                return None
            return (
                (replacement_container, replacement_pid)
                if replacement_pid != "0" and replacement_pid != before_pid
                else None
            )

        await _wait_for(replacement_process)

        async def replacement_ready() -> bool:
            logs = await asyncio.to_thread(
                subprocess.check_output,
                ["docker", "compose", "logs", "--no-color", "worker"],
                text=True,
            )
            return logs.count('"event": "worker_ready"') >= 2

        await _wait_for(replacement_ready, deadline_seconds=45)

        async def completed() -> bool:
            async with database.session() as session:
                run = await session.get(WorkflowRun, uuid.UUID(run_id))
            return run is not None and run.status.value == "succeeded"

        await _wait_for(completed, deadline_seconds=60)
        async with database.session() as session:
            decisions = await session.scalar(
                select(func.count())
                .select_from(ApprovalDecision)
                .join(ApprovalRequest)
                .where(ApprovalRequest.workflow_run_id == uuid.UUID(run_id))
            )
            projections = await session.scalar(
                select(func.count())
                .select_from(ApprovedVendor)
                .where(ApprovedVendor.workflow_run_id == run_id)
            )
            emails = await session.scalar(
                select(func.count())
                .select_from(SyntheticEmailMessage)
                .where(SyntheticEmailMessage.workflow_run_id == run_id)
            )
            sequences = list(
                (
                    await session.scalars(
                        select(WorkflowEvent.sequence).where(
                            WorkflowEvent.workflow_run_id == run_id
                        )
                    )
                ).all()
            )
            invocations = list(
                (
                    await session.scalars(
                        select(ToolInvocation).where(ToolInvocation.workflow_run_id == run_id)
                    )
                ).all()
            )
            consumptions = await session.scalar(
                select(func.count())
                .select_from(FaultInjectionConsumption)
                .where(FaultInjectionConsumption.scope_key == run_id)
            )
        assert decisions == projections == emails == 1
        assert len(sequences) == len(set(sequences))
        assert len({item.idempotency_key for item in invocations}) == len(invocations)
        assert consumptions == 1
        print(
            "Worker crash proof passed: "
            f"run={run_id} decisions={decisions} projections={projections} emails={emails} "
            "retry=temporal-redelivery"
        )
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
