"""Capability-negotiated, deterministic tools with durable invocation evidence."""

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, cast

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


@dataclass(frozen=True)
class ToolResult:
    result: dict[str, Any]
    invocation_id: str


@dataclass(frozen=True)
class ToolCapability:
    name: str
    version: str
    permissions: tuple[str, ...]


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
        if not _EXTERNAL_REFERENCE.fullmatch(external_reference):
            raise ToolInputError("external reference is invalid")
        agent = await session.get(Agent, agent_id)
        if agent is None:
            raise ToolDeniedError("workflow agent is not registered")
        tool = await session.scalar(
            select(ToolDefinition).where(ToolDefinition.name == "vendor_database_query")
        )
        if (
            tool is None
            or not tool.enabled
            or not set(tool.permissions).issubset(_agent_tool_permissions(agent))
        ):
            raise ToolDeniedError("vendor lookup is not permitted")
        existing = await session.scalar(
            select(ToolInvocation).where(
                ToolInvocation.workflow_run_id == workflow_run_id,
                ToolInvocation.idempotency_key == idempotency_key,
            )
        )
        arguments = {"external_reference": external_reference}
        fingerprint = canonical_argument_fingerprint(arguments)
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
            status=InvocationStatus.RUNNING,
            arguments=arguments,
            argument_fingerprint=fingerprint,
        )
        session.add(invocation)
        await session.flush()
        vendor = await session.scalar(
            select(Vendor).where(Vendor.external_reference == external_reference)
        )
        result = (
            {"found": True, "vendor_id": str(vendor.id), "status": vendor.status.value}
            if vendor is not None
            else {"found": False}
        )
        invocation.status = InvocationStatus.SUCCEEDED
        invocation.result_summary = result
        return ToolResult(result, str(invocation.id))
