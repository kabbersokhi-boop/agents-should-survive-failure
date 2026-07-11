"""Capability-negotiated, deterministic tools with durable invocation evidence."""

import hashlib
import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, cast

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agents_should_survive_failure.persistence.models import (
    Agent,
    InvocationStatus,
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


ToolHandler = Callable[[AsyncSession, BaseModel], Awaitable[dict[str, Any]]]


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
            )
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
            tool_version="v1",
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
    ) -> ToolResult:
        tool = await session.scalar(
            select(ToolDefinition).where(
                ToolDefinition.name == tool_name,
                ToolDefinition.version == tool_version,
            )
        )
        if tool is None:
            raise ToolUnavailableError("requested tool version is not registered")
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
            return ToolResult(existing.result_summary or {}, str(existing.id))

        invocation = ToolInvocation(
            workflow_run_id=workflow_run_id,
            tool_definition_id=tool.id,
            idempotency_key=idempotency_key,
            status=InvocationStatus.PENDING,
            arguments=arguments,
            argument_fingerprint=fingerprint,
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
            raise ToolDeniedError("vendor lookup is not permitted")
        registered = self._registry.get(tool.name)
        if registered is None:
            invocation.status = InvocationStatus.FAILED
            invocation.error_category = "handler_unavailable"
            raise ToolUnavailableError("registered tool has no local execution handler")
        try:
            validated_input = registered.input_model.model_validate(arguments)
        except ValidationError as error:
            invocation.status = InvocationStatus.FAILED
            invocation.error_category = "invalid_arguments"
            raise ToolInputError("tool arguments do not match the registered schema") from error
        invocation.status = InvocationStatus.RUNNING
        try:
            raw_result = await registered.handler(session, validated_input)
            result = registered.output_model.model_validate(raw_result).model_dump(
                exclude_none=True
            )
        except ValidationError as error:
            invocation.status = InvocationStatus.FAILED
            invocation.error_category = "invalid_result"
            raise ToolUnavailableError("tool returned an invalid result") from error
        except Exception:
            invocation.status = InvocationStatus.FAILED
            invocation.error_category = "execution_failed"
            raise
        invocation.status = InvocationStatus.SUCCEEDED
        invocation.result_summary = result
        return ToolResult(result, str(invocation.id))

    async def _vendor_lookup(self, session: AsyncSession, input: BaseModel) -> dict[str, Any]:
        request = cast(VendorLookupInput, input)
        vendor = await session.scalar(
            select(Vendor).where(Vendor.external_reference == request.external_reference)
        )
        return (
            {"found": True, "vendor_id": str(vendor.id), "status": vendor.status.value}
            if vendor is not None
            else {"found": False}
        )
