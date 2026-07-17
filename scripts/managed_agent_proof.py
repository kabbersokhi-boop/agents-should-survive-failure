"""Production-stack proof for the independently packaged Operations Investigation Agent."""

from __future__ import annotations

import asyncio
import hashlib
import os
import subprocess
import time
import uuid
from typing import Any

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import create_async_engine

from agents_should_survive_failure.persistence.models import (
    RunArtifact,
    RunBudget,
    RunCheckpoint,
    ToolInvocation,
    WorkflowEvent,
    WorkflowRun,
)
from agents_should_survive_failure.persistence.session import Database
from agents_should_survive_failure.settings import get_settings


async def _wait_for(predicate: Any, *, deadline_seconds: float = 90) -> Any:
    deadline = time.monotonic() + deadline_seconds
    while time.monotonic() < deadline:
        value = await predicate()
        if value:
            return value
        await asyncio.sleep(0.25)
    raise TimeoutError("timed out waiting for managed-agent proof evidence")


async def main() -> None:
    api_key = os.environ["INTEGRATION_API_KEY"]
    await asyncio.to_thread(
        subprocess.run,
        [
            "docker",
            "compose",
            "exec",
            "-T",
            "worker",
            "/app/.venv/bin/python",
            "-c",
            "from importlib.metadata import version; "
            "assert version('example-operations-agent') == '0.1.0'",
        ],
        check=True,
    )
    engine = create_async_engine(get_settings().database_url)
    database = Database(engine)
    headers = {"Authorization": f"Bearer {api_key}"}
    async with httpx.AsyncClient(base_url="http://127.0.0.1:8000", headers=headers) as client:
        discovered = (await client.post("/api/v1/agents/discover")).raise_for_status().json()
        assert any(
            agent["name"] == "operations-investigation" and agent["version"] == "0.1.0"
            for agent in discovered
        )
        started = (
            (
                await client.post(
                    "/api/v1/agents/operations-investigation/runs",
                    json={
                        "idempotency_key": f"managed-agent-{uuid.uuid4().hex}",
                        "version": "0.1.0",
                        "task": {
                            "incident_id": "INC-RELEASE-1",
                            "question": "retention policy",
                            "requires_approval": False,
                        },
                    },
                )
            )
            .raise_for_status()
            .json()
        )
    run_id = uuid.UUID(started["id"])

    async def completed() -> bool:
        async with database.session() as session:
            run = await session.get(WorkflowRun, run_id)
        return run is not None and run.status.value == "succeeded"

    await _wait_for(completed)
    async with database.session() as session:
        run = await session.get(WorkflowRun, run_id)
        assert run is not None and run.result_summary is not None
        assert run.result_summary["output"]["incident_id"] == "INC-RELEASE-1"
        checkpoint = await session.scalar(
            select(RunCheckpoint).where(RunCheckpoint.workflow_run_id == run_id)
        )
        artifact = await session.scalar(
            select(RunArtifact).where(RunArtifact.workflow_run_id == run_id)
        )
        budget = await session.scalar(select(RunBudget).where(RunBudget.workflow_run_id == run_id))
        tool_calls = await session.scalar(
            select(func.count())
            .select_from(ToolInvocation)
            .where(ToolInvocation.workflow_run_id == run_id)
        )
        events = await session.scalar(
            select(func.count())
            .select_from(WorkflowEvent)
            .where(WorkflowEvent.workflow_run_id == run_id)
        )
    assert checkpoint is not None
    assert (
        artifact is not None
        and hashlib.sha256(artifact.content).hexdigest() == artifact.digest_sha256
    )
    assert budget is not None and budget.consumed.get("tool_calls", 0) == 1
    assert tool_calls == 1 and events >= 3
    print(
        "Managed-agent production proof passed: "
        f"run={run_id} checkpoint={checkpoint.id} artifact={artifact.id} "
        f"tool_calls={tool_calls} events={events}"
    )
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
