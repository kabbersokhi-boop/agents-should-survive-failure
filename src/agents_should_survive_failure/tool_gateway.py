"""Permissioned, deterministic tools with invocation evidence."""

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agents_should_survive_failure.persistence.models import (
    InvocationStatus,
    ToolDefinition,
    ToolInvocation,
    Vendor,
)


class ToolDeniedError(PermissionError):
    pass


@dataclass(frozen=True)
class ToolResult:
    result: dict[str, Any]
    invocation_id: str


class ToolGateway:
    async def invoke_vendor_lookup(
        self,
        session: AsyncSession,
        *,
        workflow_run_id: str,
        permissions: set[str],
        external_reference: str,
        idempotency_key: str,
    ) -> ToolResult:
        tool = await session.scalar(
            select(ToolDefinition).where(ToolDefinition.name == "vendor_database_query")
        )
        if tool is None or not tool.enabled or not set(tool.permissions).issubset(permissions):
            raise ToolDeniedError("vendor lookup is not permitted")
        existing = await session.scalar(
            select(ToolInvocation).where(
                ToolInvocation.workflow_run_id == workflow_run_id,
                ToolInvocation.idempotency_key == idempotency_key,
            )
        )
        if existing is not None:
            return ToolResult(existing.result_summary or {}, str(existing.id))
        invocation = ToolInvocation(
            workflow_run_id=workflow_run_id,
            tool_definition_id=tool.id,
            idempotency_key=idempotency_key,
            status=InvocationStatus.RUNNING,
            arguments={"external_reference": external_reference},
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
