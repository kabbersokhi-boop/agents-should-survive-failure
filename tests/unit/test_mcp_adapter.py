from types import SimpleNamespace
from typing import Any, cast

import pytest
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import TextContent
from sqlalchemy.ext.asyncio import AsyncSession

from agents_should_survive_failure.mcp_adapter import (
    GovernedMCPAdapter,
    MCPExecutionContext,
)
from agents_should_survive_failure.tool_gateway import (
    ToolResult,
    ToolUnavailableError,
    ToolVersionMismatchError,
)


class RecordingGateway:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def invoke(self, session: AsyncSession, **kwargs: Any) -> ToolResult:
        del session
        self.calls.append(kwargs)
        results: dict[str, dict[str, Any]] = {
            "vendor_database_query": {"found": True},
            "internal_policy_search": {"citations": []},
            "synthetic_email_send": {"message_id": "message-1", "status": "simulated"},
        }
        return ToolResult(result=results[kwargs["tool_name"]], invocation_id="invocation-1")


class FailingGateway:
    def __init__(self, error: Exception) -> None:
        self._error = error

    async def invoke(self, session: AsyncSession, **kwargs: Any) -> ToolResult:
        del session, kwargs
        raise self._error


class MalformedGateway:
    async def invoke(self, session: AsyncSession, **kwargs: Any) -> ToolResult:
        del session, kwargs
        return ToolResult(result={"untrusted": "output"}, invocation_id="invocation-1")


@pytest.mark.asyncio
async def test_adapter_binds_mcp_tool_calls_to_trusted_context() -> None:
    gateway = RecordingGateway()
    adapter = GovernedMCPAdapter(cast(Any, gateway))
    context = MCPExecutionContext(
        workflow_run_id="run-1", agent_id="agent-1", correlation_id="correlation-1"
    )

    result = await adapter.call(
        cast(AsyncSession, SimpleNamespace()),
        context=context,
        tool_name="policy.search",
        arguments={"query": "approval", "limit": 1},
        idempotency_key="policy-search-1",
    )

    assert result.result == {"citations": []}
    assert gateway.calls == [
        {
            "workflow_run_id": "run-1",
            "agent_id": "agent-1",
            "tool_name": "internal_policy_search",
            "tool_version": "1",
            "arguments": {"query": "approval", "limit": 1},
            "idempotency_key": "policy-search-1",
            "correlation_id": "correlation-1:policy.search",
        }
    ]


@pytest.mark.asyncio
async def test_fastmcp_server_exposes_only_governed_tools() -> None:
    adapter = GovernedMCPAdapter(cast(Any, RecordingGateway()))
    server = adapter.server(
        cast(AsyncSession, SimpleNamespace()),
        context=MCPExecutionContext("run-1", "agent-1", "correlation-1"),
    )

    tools = await server.list_tools()

    assert {tool.name for tool in tools} == {"vendor.lookup", "policy.search", "email.send"}
    assert all("agent_id" not in str(tool.inputSchema) for tool in tools)
    content, structured = cast(
        tuple[list[TextContent], dict[str, Any]],
        await server.call_tool(
            "vendor.lookup", {"external_reference": "V-100", "idempotency_key": "lookup-1"}
        ),
    )
    assert '"found": true' in content[0].text
    assert structured["found"] is True
    _, policy = cast(
        tuple[list[TextContent], dict[str, Any]],
        await server.call_tool(
            "policy.search", {"query": "approval", "idempotency_key": "policy-1"}
        ),
    )
    _, email = cast(
        tuple[list[TextContent], dict[str, Any]],
        await server.call_tool(
            "email.send",
            {
                "recipient": "operator@example.invalid",
                "subject": "Synthetic",
                "body": "No external delivery.",
                "idempotency_key": "email-1",
            },
        ),
    )
    assert policy == {"citations": []}
    assert email == {"message_id": "message-1", "status": "simulated"}


@pytest.mark.asyncio
async def test_adapter_rejects_unknown_mcp_tools() -> None:
    adapter = GovernedMCPAdapter(cast(Any, RecordingGateway()))

    with pytest.raises(ValueError, match="not registered"):
        await adapter.call(
            cast(AsyncSession, SimpleNamespace()),
            context=MCPExecutionContext("run-1", "agent-1", "correlation-1"),
            tool_name="unknown.tool",
            arguments={},
            idempotency_key="unknown-1",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        ToolUnavailableError("MCP tool transport timed out"),
        ToolVersionMismatchError("tool version is not pinned for this workflow run"),
    ],
)
async def test_adapter_preserves_governed_gateway_failures(error: Exception) -> None:
    adapter = GovernedMCPAdapter(cast(Any, FailingGateway(error)))

    with pytest.raises(type(error)):
        await adapter.call(
            cast(AsyncSession, SimpleNamespace()),
            context=MCPExecutionContext("run-1", "agent-1", "correlation-1"),
            tool_name="vendor.lookup",
            arguments={"external_reference": "V-100"},
            idempotency_key="lookup-1",
        )


@pytest.mark.asyncio
async def test_mcp_server_rejects_malformed_gateway_output() -> None:
    adapter = GovernedMCPAdapter(cast(Any, MalformedGateway()))
    server = adapter.server(
        cast(AsyncSession, SimpleNamespace()),
        context=MCPExecutionContext("run-1", "agent-1", "correlation-1"),
    )

    with pytest.raises(ToolError, match="Error executing tool vendor.lookup"):
        await server.call_tool(
            "vendor.lookup",
            {"external_reference": "V-100", "idempotency_key": "lookup-1"},
        )
