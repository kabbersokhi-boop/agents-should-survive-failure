"""Capability-negotiated, typed tools with durable invocation evidence."""

import asyncio
import hashlib
import json
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, cast

from opentelemetry import trace
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agents_should_survive_failure.metrics import TOOL_CALLS, TOOL_LATENCY
from agents_should_survive_failure.persistence.models import (
    Agent,
    ApprovalDecision,
    ApprovalRequest,
    ApprovalStatus,
    InvocationStatus,
    PolicyDocument,
    SyntheticEmailMessage,
    ToolDefinition,
    ToolInvocation,
    Vendor,
)


class ToolDeniedError(PermissionError):
    pass


class ToolInputError(ValueError):
    pass


class ToolInvocationConflictError(ValueError):
    pass


class ToolUnavailableError(RuntimeError):
    pass


class ToolApprovalRequiredError(PermissionError):
    pass


@dataclass(frozen=True)
class ToolResult:
    result: dict[str, Any]
    invocation_id: str


@dataclass(frozen=True)
class ToolCapability:
    name: str
    version: str
    permissions: tuple[str, ...]


class VendorLookupInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    external_reference: str = Field(pattern=r"^[A-Za-z0-9._:-]{1,120}$")


class VendorLookupOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    found: bool
    vendor_id: str | None = None
    status: str | None = None


class PolicySearchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=500)
    limit: int = Field(default=5, ge=1, le=10)


class PolicyCitationOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: str
    title: str
    source_uri: str


class PolicySearchOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    citations: list[PolicyCitationOutput]


class SyntheticEmailInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    recipient: str = Field(min_length=3, max_length=320, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    subject: str = Field(min_length=1, max_length=240)
    body: str = Field(min_length=1, max_length=20_000)


class SyntheticEmailOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message_id: str
    status: str


ToolHandler = Callable[[AsyncSession, BaseModel, ToolInvocation], Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class RegisteredTool:
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    handler: ToolHandler


_EXTERNAL_REFERENCE = re.compile(r"^[A-Za-z0-9._:-]{1,120}$")


def canonical_argument_fingerprint(arguments: dict[str, Any]) -> str:
    encoded = json.dumps(arguments, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _agent_tool_permissions(agent: Agent) -> set[str]:
    configured: object = agent.configuration.get("tool_permissions", [])
    if not isinstance(configured, list):
        return set()
    values = cast(list[object], configured)
    if not all(isinstance(item, str) for item in values):
        return set()
    return {item for item in values if isinstance(item, str)}


class ToolGateway:
    def __init__(self) -> None:
        self._registry: dict[str, RegisteredTool] = {
            "vendor_database_query": RegisteredTool(
                input_model=VendorLookupInput,
                output_model=VendorLookupOutput,
                handler=self._vendor_lookup,
            ),
            "internal_policy_search": RegisteredTool(
                input_model=PolicySearchInput,
                output_model=PolicySearchOutput,
                handler=self._policy_search,
            ),
            "synthetic_email_send": RegisteredTool(
                input_model=SyntheticEmailInput,
                output_model=SyntheticEmailOutput,
                handler=self._synthetic_email_send,
            ),
        }

    async def capabilities(self, session: AsyncSession, *, agent_id: str) -> list[ToolCapability]:
        agent = await session.get(Agent, agent_id)
        if agent is None:
            return []
        permissions = _agent_tool_permissions(agent)
        tools = (
            await session.scalars(
                select(ToolDefinition)
                .where(ToolDefinition.enabled.is_(True))
                .order_by(ToolDefinition.name)
            )
        ).all()
        return [
            ToolCapability(tool.name, tool.version, tuple(tool.permissions))
            for tool in tools
            if set(tool.permissions).issubset(permissions)
        ]

    async def invoke_vendor_lookup(
        self,
        session: AsyncSession,
        *,
        workflow_run_id: str,
        agent_id: str,
        external_reference: str,
        idempotency_key: str,
    ) -> ToolResult:
        return await self.invoke(
            session,
            workflow_run_id=workflow_run_id,
            agent_id=agent_id,
            tool_name="vendor_database_query",
            tool_version="1",
            arguments={"external_reference": external_reference},
            idempotency_key=idempotency_key,
        )

    async def invoke(
        self,
        session: AsyncSession,
        *,
        workflow_run_id: str,
        agent_id: str,
        tool_name: str,
        tool_version: str,
        arguments: dict[str, Any],
        idempotency_key: str,
        correlation_id: str | None = None,
    ) -> ToolResult:
        started = time.perf_counter()
        tool = await session.scalar(
            select(ToolDefinition).where(
                ToolDefinition.name == tool_name,
                ToolDefinition.version == tool_version,
            )
        )
        if tool is None:
            self._observe("unregistered", "unknown", "unavailable", started)
            raise ToolUnavailableError("requested tool version is not registered")
        metric_name, metric_version = tool.name, tool.version
        fingerprint = canonical_argument_fingerprint(arguments)
        existing = await session.scalar(
            select(ToolInvocation).where(
                ToolInvocation.workflow_run_id == workflow_run_id,
                ToolInvocation.idempotency_key == idempotency_key,
            )
        )
        if existing is not None:
            if existing.arguments != arguments or (
                existing.argument_fingerprint not in {"legacy", fingerprint}
            ):
                raise ToolInvocationConflictError(
                    "idempotency key was reused with different tool arguments"
                )
            if existing.status is InvocationStatus.SUCCEEDED:
                self._observe(metric_name, metric_version, "idempotent_replay", started)
                return ToolResult(existing.result_summary or {}, str(existing.id))
            if existing.status is InvocationStatus.DENIED:
                self._observe(metric_name, metric_version, "denied", started)
                raise ToolDeniedError("tool invocation was previously denied")
            if existing.error_category == "approval_required":
                self._observe(metric_name, metric_version, "approval_required", started)
                raise ToolApprovalRequiredError("tool invocation requires an approved decision")
            self._observe(metric_name, metric_version, "unavailable", started)
            raise ToolUnavailableError("tool invocation did not complete successfully")

        invocation = ToolInvocation(
            workflow_run_id=workflow_run_id,
            tool_definition_id=tool.id,
            idempotency_key=idempotency_key,
            status=InvocationStatus.PENDING,
            arguments=arguments,
            argument_fingerprint=fingerprint,
            correlation_id=correlation_id or f"{workflow_run_id}:{idempotency_key}",
        )
        session.add(invocation)
        await session.flush()
        agent = await session.get(Agent, agent_id)
        if (
            agent is None
            or not tool.enabled
            or not set(tool.permissions).issubset(_agent_tool_permissions(agent))
        ):
            invocation.status = InvocationStatus.DENIED
            invocation.error_category = "policy_denied"
            self._observe(metric_name, metric_version, "denied", started)
            raise ToolDeniedError("tool invocation is not permitted")
        if tool.approval_required and not await self._has_approved_decision(
            session, workflow_run_id
        ):
            invocation.status = InvocationStatus.DENIED
            invocation.error_category = "approval_required"
            self._observe(metric_name, metric_version, "approval_required", started)
            raise ToolApprovalRequiredError("tool invocation requires an approved decision")
        registered = self._registry.get(tool.name)
        if registered is None:
            invocation.status = InvocationStatus.FAILED
            invocation.error_category = "handler_unavailable"
            self._observe(metric_name, metric_version, "unavailable", started)
            raise ToolUnavailableError("registered tool has no local execution handler")
        try:
            validated_input = registered.input_model.model_validate(arguments)
        except ValidationError as error:
            invocation.status = InvocationStatus.FAILED
            invocation.error_category = "invalid_arguments"
            self._observe(metric_name, metric_version, "invalid_input", started)
            raise ToolInputError("tool arguments do not match the registered schema") from error
        invocation.status = InvocationStatus.RUNNING
        try:
            with trace.get_tracer(__name__).start_as_current_span("agents.tool.invoke") as span:
                span.set_attribute("agents.tool.name", metric_name)
                span.set_attribute("agents.tool.version", metric_version)
                async with asyncio.timeout(tool.timeout_seconds):
                    raw_result = await registered.handler(session, validated_input, invocation)
            result = registered.output_model.model_validate(raw_result).model_dump(
                exclude_none=True
            )
        except TimeoutError as error:
            invocation.status = InvocationStatus.FAILED
            invocation.error_category = "timeout"
            self._observe(metric_name, metric_version, "timeout", started)
            raise ToolUnavailableError("tool invocation exceeded its timeout") from error
        except ValidationError as error:
            invocation.status = InvocationStatus.FAILED
            invocation.error_category = "invalid_result"
            self._observe(metric_name, metric_version, "invalid_output", started)
            raise ToolUnavailableError("tool returned an invalid result") from error
        except Exception:
            invocation.status = InvocationStatus.FAILED
            invocation.error_category = "execution_failed"
            self._observe(metric_name, metric_version, "failed", started)
            raise
        invocation.status = InvocationStatus.SUCCEEDED
        invocation.result_summary = result
        self._observe(metric_name, metric_version, "succeeded", started)
        return ToolResult(result, str(invocation.id))

    @staticmethod
    def _observe(name: str, version: str, outcome: str, started: float) -> None:
        TOOL_CALLS.labels(name, version, outcome).inc()
        TOOL_LATENCY.labels(name, version, outcome).observe(time.perf_counter() - started)

    async def _vendor_lookup(
        self, session: AsyncSession, input: BaseModel, invocation: ToolInvocation
    ) -> dict[str, Any]:
        del invocation
        request = cast(VendorLookupInput, input)
        vendor = await session.scalar(
            select(Vendor).where(Vendor.external_reference == request.external_reference)
        )
        return (
            {"found": True, "vendor_id": str(vendor.id), "status": vendor.status.value}
            if vendor is not None
            else {"found": False}
        )

    async def _policy_search(
        self, session: AsyncSession, input: BaseModel, invocation: ToolInvocation
    ) -> dict[str, Any]:
        del invocation
        request = cast(PolicySearchInput, input)
        query_terms = [term.lower() for term in request.query.split() if term]
        documents = (
            await session.scalars(
                select(PolicyDocument).order_by(PolicyDocument.title, PolicyDocument.chunk_index)
            )
        ).all()
        matching = [
            document
            for document in documents
            if any(
                term in document.title.lower() or term in document.content.lower()
                for term in query_terms
            )
        ][: request.limit]
        return {
            "citations": [
                {
                    "document_id": str(document.id),
                    "title": document.title,
                    "source_uri": document.source_uri,
                }
                for document in matching
            ]
        }

    async def _synthetic_email_send(
        self, session: AsyncSession, input: BaseModel, invocation: ToolInvocation
    ) -> dict[str, Any]:
        request = cast(SyntheticEmailInput, input)
        message = await session.scalar(
            select(SyntheticEmailMessage).where(
                SyntheticEmailMessage.workflow_run_id == invocation.workflow_run_id,
                SyntheticEmailMessage.idempotency_key == invocation.idempotency_key,
            )
        )
        if message is None:
            message = SyntheticEmailMessage(
                workflow_run_id=invocation.workflow_run_id,
                idempotency_key=invocation.idempotency_key,
                recipient=request.recipient,
                subject=request.subject,
                body=request.body,
                status="simulated",
            )
            session.add(message)
            await session.flush()
        return {"message_id": str(message.id), "status": message.status}

    async def _has_approved_decision(self, session: AsyncSession, workflow_run_id: str) -> bool:
        decision = await session.scalar(
            select(ApprovalDecision.id)
            .join(ApprovalRequest, ApprovalRequest.id == ApprovalDecision.approval_request_id)
            .where(
                ApprovalRequest.workflow_run_id == workflow_run_id,
                ApprovalRequest.status == ApprovalStatus.APPROVED,
                ApprovalDecision.decision == ApprovalStatus.APPROVED,
            )
        )
        return decision is not None
