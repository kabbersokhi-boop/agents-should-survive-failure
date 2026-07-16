"""Governed activity host for trusted public-SDK managed agent packages."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any, cast

from agents_should_survive_failure_sdk import (
    AgentArtifact,
    AgentResult,
    AgentTask,
    ArtifactReference,
    BudgetRequirements,
    CancellationRequested,
    CapabilityDenied,
    CheckpointReference,
    ManagedAgent,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from temporalio import activity

from agents_should_survive_failure.agent_discovery import load_installed_agent
from agents_should_survive_failure.failures import temporal_failure
from agents_should_survive_failure.persistence.models import (
    Agent,
    AuditEvent,
    RunStatus,
    WorkflowEvent,
    WorkflowRun,
)
from agents_should_survive_failure.persistence.repositories import AuditEventRepository
from agents_should_survive_failure.persistence.session import Database
from agents_should_survive_failure.runtime_state import (
    consume_budget,
    create_artifact,
    initialize_budget,
    load_checkpoint,
    save_checkpoint,
)
from agents_should_survive_failure.tool_gateway import ToolGateway
from agents_should_survive_failure.workflows.contracts import ManagedAgentInput


class ManagedActivityContext:
    """Activity-local implementation of public services, constrained by the pinned manifest."""

    def __init__(
        self,
        *,
        session: AsyncSession,
        run: WorkflowRun,
        agent: ManagedAgent,
        gateway: ToolGateway,
    ) -> None:
        self._session = session
        self._run = run
        self._agent = agent
        self._gateway = gateway

    @property
    def run_id(self) -> str:
        return str(self._run.id)

    @property
    def correlation_id(self) -> str:
        return f"{self._run.id}:managed-agent"

    async def emit_event(self, event_type: str, summary: str, payload: Mapping[str, Any]) -> None:
        if not event_type or len(event_type) > 120 or not summary or len(summary) > 4_000:
            raise ValueError("agent event is invalid")
        existing = (
            await self._session.scalars(
                select(WorkflowEvent)
                .where(WorkflowEvent.workflow_run_id == self._run.id)
                .order_by(WorkflowEvent.sequence.desc())
                .limit(1)
            )
        ).first()
        sequence = (existing.sequence if existing is not None else 0) + 10
        self._session.add(
            WorkflowEvent(
                workflow_run_id=self._run.id,
                sequence=sequence,
                event_type=event_type,
                summary=summary,
                payload=dict(payload),
            )
        )
        await AuditEventRepository(self._session).append(
            AuditEvent(
                workflow_run_id=self._run.id,
                action="managed_agent.event",
                resource_type="workflow_run",
                resource_id=self._run.id,
                idempotency_key=f"{self._run.id}:managed-agent:event:{sequence}",
                summary="Managed agent emitted bounded progress evidence.",
                evidence={"event_type": event_type},
            )
        )

    async def call_tool(
        self, name: str, arguments: Mapping[str, Any], *, idempotency_key: str
    ) -> Mapping[str, Any]:
        declaration = next((tool for tool in self._agent.metadata.tools if tool.name == name), None)
        if declaration is None:
            raise CapabilityDenied("tool is not declared by the pinned agent manifest")
        await consume_budget(self._session, workflow_run_id=self._run.id, amount={"tool_calls": 1})
        result = await self._gateway.invoke(
            self._session,
            workflow_run_id=str(self._run.id),
            agent_id=str(self._run.agent_id),
            tool_name=declaration.name,
            tool_version=declaration.version,
            arguments=dict(arguments),
            idempotency_key=idempotency_key,
            correlation_id=self.correlation_id,
        )
        return cast(Mapping[str, Any], result.result)

    async def request_approval(self, summary: str) -> bool:
        del summary
        raise CapabilityDenied(
            "managed-agent approval waits are not available in this activity action"
        )

    async def save_checkpoint(
        self, name: str, schema_version: str, value: Mapping[str, Any]
    ) -> CheckpointReference:
        if not self._agent.metadata.checkpoint_supported:
            raise CapabilityDenied("checkpoint capability is not declared")
        checkpoint = await save_checkpoint(
            self._session,
            workflow_run_id=self._run.id,
            agent_id=self._run.agent_id,
            name=name,
            schema_version=schema_version,
            value=dict(value),
            maximum_bytes=self._agent.metadata.budget_defaults.max_checkpoint_bytes,
        )
        await consume_budget(
            self._session,
            workflow_run_id=self._run.id,
            amount={"checkpoint_bytes": checkpoint.size_bytes},
        )
        return CheckpointReference(
            name=checkpoint.name,
            schema_version=checkpoint.schema_version,
            digest_sha256=checkpoint.digest_sha256,
        )

    async def load_checkpoint(self, name: str) -> Mapping[str, Any] | None:
        checkpoint = await load_checkpoint(self._session, workflow_run_id=self._run.id, name=name)
        return checkpoint.value if checkpoint is not None else None

    async def create_artifact(self, artifact: AgentArtifact) -> ArtifactReference:
        if not self._agent.metadata.artifact_supported:
            raise CapabilityDenied("artifact capability is not declared")
        created = await create_artifact(
            self._session,
            workflow_run_id=self._run.id,
            agent_id=self._run.agent_id,
            name=artifact.name,
            content_type=artifact.content_type,
            content=artifact.content,
            maximum_bytes=self._agent.metadata.budget_defaults.max_artifact_bytes,
        )
        await consume_budget(
            self._session,
            workflow_run_id=self._run.id,
            amount={"artifact_bytes": created.size_bytes},
        )
        return ArtifactReference(
            artifact_id=str(created.id),
            digest_sha256=created.digest_sha256,
            content_type=created.content_type,
            size_bytes=created.size_bytes,
        )

    async def remaining_budget(self) -> Mapping[str, int]:
        budget = await initialize_budget(
            self._session,
            workflow_run_id=self._run.id,
            limits=_budget_limits(self._agent.metadata.budget_defaults),
        )
        return {key: limit - budget.consumed.get(key, 0) for key, limit in budget.limits.items()}

    async def check_cancelled(self) -> None:
        run = await self._session.get(WorkflowRun, self._run.id)
        if run is not None and run.status is RunStatus.CANCELLED:
            raise CancellationRequested("run was cancelled")

    async def delegate(
        self, agent_slug: str, task: AgentTask, *, budget: BudgetRequirements
    ) -> AgentResult:
        del agent_slug, task, budget
        raise CapabilityDenied("managed-agent delegation is not available in this activity action")

    async def call_model(self, input: Mapping[str, Any]) -> Mapping[str, Any]:
        del input
        raise CapabilityDenied(
            "managed-agent model calls are not available in this activity action"
        )


def _budget_limits(requirements: BudgetRequirements) -> dict[str, int]:
    return {
        "tool_calls": requirements.max_tool_calls,
        "model_calls": requirements.max_model_calls,
        "checkpoint_bytes": requirements.max_checkpoint_bytes,
        "artifact_bytes": requirements.max_artifact_bytes,
        "child_agents": requirements.max_child_agents,
    }


class ManagedAgentActivities:
    """Execute installed trusted agent packages only through constrained public SDK services."""

    def __init__(self, database: Database, gateway: ToolGateway) -> None:
        self._database = database
        self._gateway = gateway

    @activity.defn(name="managed_agent.execute")
    async def execute(self, input: ManagedAgentInput) -> dict[str, object]:
        run_id = uuid.UUID(input.run_id)
        try:
            async with self._database.session() as session:
                run = await session.get(WorkflowRun, run_id)
                if run is None:
                    raise ValueError("managed agent run does not exist")
                agent_row = await session.get(Agent, run.agent_id)
                if agent_row is None or agent_row.workflow_type != "managed_agent":
                    raise ValueError("managed agent version is unavailable")
                agent = load_installed_agent(
                    package_name=agent_row.package_name,
                    entry_point=agent_row.entry_point,
                )
                if (
                    agent.metadata.slug != agent_row.name
                    or agent.metadata.version != agent_row.version
                    or agent_row.integrity_digest
                    != agent_row.configuration.get("registration_sha256")
                ):
                    raise ValueError("installed agent does not match its immutable registration")
                task_input = run.input_summary.get("task")
                if not isinstance(task_input, dict):
                    raise ValueError("managed agent task is invalid")
                run.status = RunStatus.RUNNING
                await initialize_budget(
                    session,
                    workflow_run_id=run.id,
                    limits=_budget_limits(agent.metadata.budget_defaults),
                )
                context = ManagedActivityContext(
                    session=session,
                    run=run,
                    agent=agent,
                    gateway=self._gateway,
                )
                result = await agent.run(AgentTask(input=cast(dict[str, Any], task_input)), context)
                for artifact in result.artifacts:
                    await context.create_artifact(artifact)
                run.status = RunStatus.SUCCEEDED
                run.result_summary = {"summary": result.summary, "output": dict(result.output)}
                await context.emit_event("managed_agent.completed", result.summary, {})
                return {"summary": result.summary, "output": dict(result.output)}
        except Exception as error:
            raise temporal_failure(error) from error
