"""Governed activity host for trusted public-SDK managed agent packages."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Mapping
from typing import Any, cast

from agents_should_survive_failure_sdk import (
    AgentArtifact,
    AgentMetadata,
    AgentResult,
    AgentTask,
    ArtifactReference,
    BudgetRequirements,
    CancellationRequested,
    Capability,
    CapabilityDenied,
    CheckpointReference,
)
from sqlalchemy import select
from temporalio import activity

from agents_should_survive_failure.agent_discovery import load_installed_agent
from agents_should_survive_failure.failures import temporal_failure
from agents_should_survive_failure.persistence.models import (
    Agent,
    ApprovalRequest,
    ApprovalStatus,
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
    read_artifact,
    save_checkpoint,
)
from agents_should_survive_failure.tool_gateway import ToolGateway
from agents_should_survive_failure.workflows.contracts import ManagedAgentInput


class ManagedActivityContext:
    """Activity-local implementation of public services, constrained by the pinned manifest."""

    def __init__(
        self,
        *,
        database: Database,
        run_id: uuid.UUID,
        agent_id: uuid.UUID,
        metadata: AgentMetadata,
        gateway: ToolGateway,
    ) -> None:
        self._database = database
        self._run_id = run_id
        self._agent_id = agent_id
        self._metadata = metadata
        self._gateway = gateway

    @property
    def run_id(self) -> str:
        return str(self._run_id)

    @property
    def correlation_id(self) -> str:
        return f"{self._run_id}:managed-agent"

    def _has_capability(self, capability: Capability) -> bool:
        return capability in self._metadata.required_capabilities

    async def emit_event(self, event_type: str, summary: str, payload: Mapping[str, Any]) -> None:
        if not event_type or len(event_type) > 120 or not summary or len(summary) > 4_000:
            raise ValueError("agent event is invalid")
        async with self._database.session() as session:
            run = await session.scalar(
                select(WorkflowRun).where(WorkflowRun.id == self._run_id).with_for_update()
            )
            if run is None:
                raise ValueError("managed agent run does not exist")
            existing = (
                await session.scalars(
                    select(WorkflowEvent)
                    .where(WorkflowEvent.workflow_run_id == self._run_id)
                    .order_by(WorkflowEvent.sequence.desc())
                    .limit(1)
                )
            ).first()
            sequence = (existing.sequence if existing is not None else 0) + 10
            session.add(
                WorkflowEvent(
                    workflow_run_id=self._run_id,
                    sequence=sequence,
                    event_type=event_type,
                    summary=summary,
                    payload=dict(payload),
                )
            )
            await AuditEventRepository(session).append(
                AuditEvent(
                    workflow_run_id=self._run_id,
                    action="managed_agent.event",
                    resource_type="workflow_run",
                    resource_id=self._run_id,
                    idempotency_key=f"{self._run_id}:managed-agent:event:{sequence}",
                    summary="Managed agent emitted bounded progress evidence.",
                    evidence={"event_type": event_type},
                )
            )

    async def call_tool(
        self, name: str, arguments: Mapping[str, Any], *, idempotency_key: str
    ) -> Mapping[str, Any]:
        declaration = next((tool for tool in self._metadata.tools if tool.name == name), None)
        if declaration is None or not self._has_capability(Capability.TOOLS):
            raise CapabilityDenied("tool is not declared by the pinned agent manifest")
        async with self._database.session() as session:
            await consume_budget(session, workflow_run_id=self._run_id, amount={"tool_calls": 1})
            result = await self._gateway.invoke(
                session,
                workflow_run_id=str(self._run_id),
                agent_id=str(self._agent_id),
                tool_name=declaration.name,
                tool_version=declaration.version,
                arguments=dict(arguments),
                idempotency_key=idempotency_key,
                correlation_id=self.correlation_id,
            )
        return cast(Mapping[str, Any], result.result)

    async def request_approval(self, summary: str) -> bool:
        if not self._has_capability(Capability.APPROVALS) and not self._metadata.approval_required:
            raise CapabilityDenied("approval capability is not declared")
        if not summary or len(summary) > 4_000:
            raise ValueError("approval summary is invalid")
        request_key = "managed-agent-approval"
        created_approval_id: str | None = None
        async with self._database.session() as session:
            run = await session.scalar(
                select(WorkflowRun).where(WorkflowRun.id == self._run_id).with_for_update()
            )
            if run is None:
                raise ValueError("managed agent run does not exist")
            if run.status is RunStatus.CANCELLED:
                raise CancellationRequested("run was cancelled")
            approval = await session.scalar(
                select(ApprovalRequest).where(
                    ApprovalRequest.workflow_run_id == self._run_id,
                    ApprovalRequest.request_key == request_key,
                )
            )
            if approval is None:
                approval = ApprovalRequest(
                    workflow_run_id=self._run_id,
                    request_key=request_key,
                    status=ApprovalStatus.PENDING,
                    summary=summary,
                )
                session.add(approval)
                run.status = RunStatus.WAITING
                await session.flush()
                created_approval_id = str(approval.id)
        if created_approval_id is not None:
            await self.emit_event(
                "managed_agent.approval_requested",
                "Managed agent requested authorized approval.",
                {"approval_request_id": created_approval_id},
            )
        while True:
            async with self._database.session() as session:
                run = await session.get(WorkflowRun, self._run_id)
                approval = await session.scalar(
                    select(ApprovalRequest).where(
                        ApprovalRequest.workflow_run_id == self._run_id,
                        ApprovalRequest.request_key == request_key,
                    )
                )
            if run is None or approval is None:
                raise ValueError("managed agent approval state is unavailable")
            if run.status is RunStatus.CANCELLED or approval.status is ApprovalStatus.CANCELLED:
                raise CancellationRequested("run was cancelled")
            if approval.status is ApprovalStatus.APPROVED:
                return True
            if approval.status is ApprovalStatus.REJECTED:
                return False
            activity.heartbeat("waiting for managed-agent approval")
            await asyncio.sleep(0.5)

    async def save_checkpoint(
        self, name: str, schema_version: str, value: Mapping[str, Any]
    ) -> CheckpointReference:
        if (
            not self._metadata.checkpoint_supported
            or not self._has_capability(Capability.CHECKPOINTS)
        ):
            raise CapabilityDenied("checkpoint capability is not declared")
        async with self._database.session() as session:
            checkpoint = await save_checkpoint(
                session,
                workflow_run_id=self._run_id,
                agent_id=self._agent_id,
                name=name,
                schema_version=schema_version,
                value=dict(value),
                maximum_bytes=self._metadata.budget_defaults.max_checkpoint_bytes,
            )
            await consume_budget(
                session,
                workflow_run_id=self._run_id,
                amount={"checkpoint_bytes": checkpoint.size_bytes},
            )
        return CheckpointReference(
            name=checkpoint.name,
            schema_version=checkpoint.schema_version,
            digest_sha256=checkpoint.digest_sha256,
        )

    async def load_checkpoint(self, name: str) -> Mapping[str, Any] | None:
        async with self._database.session() as session:
            checkpoint = await load_checkpoint(session, workflow_run_id=self._run_id, name=name)
        return checkpoint.value if checkpoint is not None else None

    async def create_artifact(self, artifact: AgentArtifact) -> ArtifactReference:
        if not self._metadata.artifact_supported or not self._has_capability(Capability.ARTIFACTS):
            raise CapabilityDenied("artifact capability is not declared")
        async with self._database.session() as session:
            created = await create_artifact(
                session,
                workflow_run_id=self._run_id,
                agent_id=self._agent_id,
                name=artifact.name,
                content_type=artifact.content_type,
                content=artifact.content,
                maximum_bytes=self._metadata.budget_defaults.max_artifact_bytes,
            )
            await consume_budget(
                session,
                workflow_run_id=self._run_id,
                amount={"artifact_bytes": created.size_bytes},
            )
        return ArtifactReference(
            artifact_id=str(created.id),
            digest_sha256=created.digest_sha256,
            content_type=created.content_type,
            size_bytes=created.size_bytes,
        )

    async def read_artifact(self, artifact_id: str) -> AgentArtifact:
        try:
            artifact_uuid = uuid.UUID(artifact_id)
        except ValueError as error:
            raise CapabilityDenied("artifact identifier is invalid") from error
        async with self._database.session() as session:
            artifact = await read_artifact(
                session,
                workflow_run_id=self._run_id,
                agent_id=self._agent_id,
                artifact_id=artifact_uuid,
            )
        return AgentArtifact(
            name=artifact.name,
            content_type=artifact.content_type,
            content=artifact.content,
        )

    async def remaining_budget(self) -> Mapping[str, int]:
        async with self._database.session() as session:
            budget = await initialize_budget(
                session,
                workflow_run_id=self._run_id,
                limits=_budget_limits(self._metadata.budget_defaults),
            )
        return {key: limit - budget.consumed.get(key, 0) for key, limit in budget.limits.items()}

    async def check_cancelled(self) -> None:
        async with self._database.session() as session:
            run = await session.get(WorkflowRun, self._run_id)
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
                if run.status is RunStatus.CANCELLED:
                    return {"cancelled": True}
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
                agent_id = run.agent_id
            context = ManagedActivityContext(
                database=self._database,
                run_id=run_id,
                agent_id=agent_id,
                metadata=agent.metadata,
                gateway=self._gateway,
            )
            result = await agent.run(AgentTask(input=cast(dict[str, Any], task_input)), context)
            for artifact in result.artifacts:
                await context.create_artifact(artifact)
            async with self._database.session() as session:
                run = await session.scalar(
                    select(WorkflowRun).where(WorkflowRun.id == run_id).with_for_update()
                )
                if run is None:
                    raise ValueError("managed agent run does not exist")
                if run.status is RunStatus.CANCELLED:
                    return {"cancelled": True}
                run.status = RunStatus.SUCCEEDED
                run.result_summary = {"summary": result.summary, "output": dict(result.output)}
            await context.emit_event("managed_agent.completed", result.summary, {})
            return {"summary": result.summary, "output": dict(result.output)}
        except Exception as error:
            raise temporal_failure(error) from error
