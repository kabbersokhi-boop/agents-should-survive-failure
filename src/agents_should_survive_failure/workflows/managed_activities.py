"""Governed activity host for trusted public-SDK managed agent packages."""

from __future__ import annotations

import asyncio
import hashlib
import json
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
from sqlalchemy import func, select
from temporalio import activity

from agents_should_survive_failure.agent_discovery import load_installed_agent
from agents_should_survive_failure.failures import temporal_failure
from agents_should_survive_failure.json_schema import validate_json_schema
from agents_should_survive_failure.model_evidence import ModelEvidenceService
from agents_should_survive_failure.persistence.models import (
    Agent,
    AgentStatus,
    AgentToolGrant,
    ApprovalRequest,
    ApprovalStatus,
    AuditEvent,
    RunBudget,
    RunDelegation,
    RunStatus,
    RunToolGrantSnapshot,
    WorkflowEvent,
    WorkflowRun,
)
from agents_should_survive_failure.persistence.repositories import AuditEventRepository
from agents_should_survive_failure.persistence.session import Database
from agents_should_survive_failure.providers import ModelProvider
from agents_should_survive_failure.runtime_state import (
    consume_budget,
    create_artifact,
    initialize_budget,
    load_checkpoint,
    read_artifact,
    save_checkpoint,
)
from agents_should_survive_failure.tool_gateway import ToolGateway
from agents_should_survive_failure.workflow_starts import (
    TemporalWorkflowClient,
    WorkflowStartCoordinator,
)
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
        model_provider: ModelProvider,
        temporal_client: TemporalWorkflowClient,
    ) -> None:
        self._database = database
        self._run_id = run_id
        self._agent_id = agent_id
        self._metadata = metadata
        self._gateway = gateway
        self._model_provider = model_provider
        self._temporal_client = temporal_client

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
            await consume_budget(
                session,
                workflow_run_id=self._run_id,
                amount={"steps": 1, "tool_calls": 1},
            )
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
        if not self._metadata.checkpoint_supported or not self._has_capability(
            Capability.CHECKPOINTS
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
                amount={"checkpoint_bytes": checkpoint.size_bytes, "steps": 1},
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
                amount={"artifact_bytes": created.size_bytes, "steps": 1},
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
        if not self._has_capability(Capability.DELEGATION):
            raise CapabilityDenied("delegation capability is not declared")
        policy = self._metadata.delegation_policy
        if agent_slug not in policy.allowed_agent_slugs:
            raise CapabilityDenied("child agent is not allowed by the pinned delegation policy")
        task_payload = dict(task.input)
        requested_limits = _budget_limits(budget)
        request_digest = hashlib.sha256(
            json.dumps(
                {
                    "agent_slug": agent_slug,
                    "budget": requested_limits,
                    "task": task_payload,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        idempotency_key = f"{self._run_id}:delegate:{request_digest}"
        async with self._database.session() as session:
            parent = await session.scalar(
                select(WorkflowRun).where(WorkflowRun.id == self._run_id).with_for_update()
            )
            if parent is None:
                raise ValueError("managed agent parent run does not exist")
            if parent.status is RunStatus.CANCELLED:
                raise CancellationRequested("run was cancelled")
            child = await session.scalar(
                select(Agent)
                .where(
                    Agent.name == agent_slug,
                    Agent.workflow_type == "managed_agent",
                    Agent.status == AgentStatus.ACTIVE,
                )
                .order_by(Agent.created_at.desc(), Agent.id.desc())
            )
            if child is None:
                raise CapabilityDenied("allowed child agent is not installed and enabled")
            child_metadata = AgentMetadata.model_validate(child.manifest)
            child_depth = parent.delegation_depth + 1
            if child_depth > policy.max_depth or child_depth > budget.max_delegation_depth:
                raise CapabilityDenied("delegation exceeds the pinned maximum depth")
            child_count = await session.scalar(
                select(func.count())
                .select_from(RunDelegation)
                .where(RunDelegation.parent_workflow_run_id == parent.id)
            )
            if child_count is None or child_count >= policy.max_fan_out:
                raise CapabilityDenied("delegation exceeds the pinned maximum fan-out")
            ancestor_agent_ids = {parent.agent_id}
            ancestor = parent
            while ancestor.parent_workflow_run_id is not None:
                ancestor = await session.get(WorkflowRun, ancestor.parent_workflow_run_id)
                if ancestor is None:
                    raise ValueError("delegation lineage is incomplete")
                ancestor_agent_ids.add(ancestor.agent_id)
            if child.id in ancestor_agent_ids:
                raise CapabilityDenied("delegation would introduce an agent cycle")
            parent_budget = await session.scalar(
                select(RunBudget).where(RunBudget.workflow_run_id == parent.id).with_for_update()
            )
            if parent_budget is None:
                raise ValueError("parent budget is not initialized")
            child_maximum = _budget_limits(child_metadata.budget_defaults)
            for name, value in requested_limits.items():
                remaining = parent_budget.limits.get(name, 0) - parent_budget.consumed.get(name, 0)
                if value > remaining or value > child_maximum.get(name, 0):
                    raise CapabilityDenied("delegated budget exceeds parent or child authority")
            parent_tool_ids = set(
                (
                    await session.scalars(
                        select(RunToolGrantSnapshot.tool_definition_id).where(
                            RunToolGrantSnapshot.workflow_run_id == parent.id
                        )
                    )
                ).all()
            )
            child_tool_ids = set(
                (
                    await session.scalars(
                        select(AgentToolGrant.tool_definition_id).where(
                            AgentToolGrant.agent_id == child.id
                        )
                    )
                ).all()
            )
            if not child_tool_ids.issubset(parent_tool_ids):
                raise CapabilityDenied(
                    "child tool grants are not attenuated by the parent snapshot"
                )
            requested_by_id = parent.requested_by_id
            root_run_id = parent.root_workflow_run_id
        coordinator = WorkflowStartCoordinator(self._database, self._temporal_client)
        child_run = await coordinator.create_or_get_managed_agent(
            requested_by_id=requested_by_id,
            agent_id=child.id,
            task=cast(dict[str, object], task_payload),
            idempotency_key=idempotency_key,
            parent_run_id=self._run_id,
            root_run_id=root_run_id,
            delegation_depth=child_depth,
            delegated_budget_limits=requested_limits,
            allowed_tool_definition_ids=child_tool_ids,
        )
        await coordinator.start(child_run.id)
        handle = self._temporal_client.get_workflow_handle(child_run.temporal_workflow_id)
        child_result = await handle.result()
        if child_result is None:
            raise CancellationRequested("delegated child run was cancelled")
        if not isinstance(child_result, dict):
            raise ValueError("delegated child run returned an invalid result")
        child_result_data = cast(dict[str, object], child_result)
        output = child_result_data.get("output")
        summary = child_result_data.get("summary")
        if not isinstance(output, dict) or not isinstance(summary, str):
            raise ValueError("delegated child run returned an invalid result")
        return AgentResult(output=cast(dict[str, Any], output), summary=summary)

    async def call_model(self, input: Mapping[str, Any]) -> Mapping[str, Any]:
        if not self._has_capability(Capability.MODELS):
            raise CapabilityDenied("model capability is not declared")
        try:
            prompt = json.dumps(dict(input), sort_keys=True, separators=(",", ":"))
        except (TypeError, ValueError) as error:
            raise ValueError("model input must be JSON serializable") from error
        if not prompt or len(prompt.encode("utf-8")) > 16_000:
            raise ValueError("model input is invalid or exceeds the bounded size limit")
        response: object | None = None
        failure: Exception | None = None
        async with self._database.session() as session:
            await consume_budget(
                session,
                workflow_run_id=self._run_id,
                amount={"model_calls": 1, "steps": 1},
            )
            try:
                response = await ModelEvidenceService(self._model_provider).explain(
                    session,
                    workflow_run_id=self._run_id,
                    prompt=prompt,
                    correlation_id=f"{self.correlation_id}:model",
                )
            except Exception as error:
                failure = error
            else:
                await consume_budget(
                    session,
                    workflow_run_id=self._run_id,
                    amount={
                        "input_tokens": response.input_tokens,
                        "output_tokens": response.output_tokens,
                    },
                )
        if failure is not None:
            raise failure
        assert response is not None
        return {
            "provider": response.provider,
            "model": response.model,
            "summary": response.summary,
        }


def _budget_limits(requirements: BudgetRequirements) -> dict[str, int]:
    return {
        "artifact_bytes": requirements.max_artifact_bytes,
        "checkpoint_bytes": requirements.max_checkpoint_bytes,
        "child_agents": requirements.max_child_agents,
        "estimated_cost_microunits": requirements.max_estimated_cost_microunits,
        "input_tokens": requirements.max_input_tokens,
        "model_calls": requirements.max_model_calls,
        "output_tokens": requirements.max_output_tokens,
        "steps": requirements.max_steps,
        "tool_calls": requirements.max_tool_calls,
    }


class ManagedAgentActivities:
    """Execute installed trusted agent packages only through constrained public SDK services."""

    def __init__(
        self,
        database: Database,
        gateway: ToolGateway,
        model_provider: ModelProvider,
        temporal_client: TemporalWorkflowClient,
    ) -> None:
        self._database = database
        self._gateway = gateway
        self._model_provider = model_provider
        self._temporal_client = temporal_client

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
                validate_json_schema(
                    cast(dict[str, object], task_input),
                    dict(agent.metadata.input_schema),
                    label="task input",
                )
                run.status = RunStatus.RUNNING
                existing_budget = await session.scalar(
                    select(RunBudget).where(RunBudget.workflow_run_id == run.id)
                )
                if existing_budget is None:
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
                model_provider=self._model_provider,
                temporal_client=self._temporal_client,
            )
            result = await agent.run(AgentTask(input=cast(dict[str, Any], task_input)), context)
            validate_json_schema(
                dict(result.output), dict(agent.metadata.output_schema), label="agent output"
            )
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
