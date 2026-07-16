"""MCP adapter that binds tool calls to a platform-owned execution context.

The adapter deliberately accepts no caller-supplied agent identity or permissions. A managed runner
constructs one adapter per run and may expose its FastMCP server over an authenticated local
transport. It is not a public unauthenticated tool endpoint.
"""

from dataclasses import dataclass
from typing import Annotated, Any, ClassVar

from mcp.server.fastmcp import FastMCP
from pydantic import Field
from sqlalchemy.ext.asyncio import AsyncSession

from agents_should_survive_failure.tool_gateway import (
    PolicySearchOutput,
    SyntheticEmailOutput,
    ToolGateway,
    ToolResult,
    VendorLookupOutput,
)
from agents_should_survive_failure.workflows.contracts import GovernedToolName


@dataclass(frozen=True)
class MCPExecutionContext:
    """Trusted run context injected by the managed execution host."""

    workflow_run_id: str
    agent_id: str
    correlation_id: str


class GovernedMCPAdapter:
    """Translate the three MCP tools into policy-enforced gateway calls."""

    _TOOL_VERSIONS: ClassVar[dict[str, tuple[str, str]]] = {
        "vendor.lookup": (GovernedToolName.VENDOR_DATABASE_QUERY.value, "1"),
        "policy.search": (GovernedToolName.INTERNAL_POLICY_SEARCH.value, "1"),
        "email.send": (GovernedToolName.SYNTHETIC_EMAIL_SEND.value, "1"),
    }

    def __init__(self, gateway: ToolGateway | None = None) -> None:
        self._gateway = gateway or ToolGateway()

    async def call(
        self,
        session: AsyncSession,
        *,
        context: MCPExecutionContext,
        tool_name: str,
        arguments: dict[str, Any],
        idempotency_key: str,
    ) -> ToolResult:
        target = self._TOOL_VERSIONS.get(tool_name)
        if target is None:
            raise ValueError("requested MCP tool is not registered")
        gateway_name, version = target
        return await self._gateway.invoke(
            session,
            workflow_run_id=context.workflow_run_id,
            agent_id=context.agent_id,
            tool_name=gateway_name,
            tool_version=version,
            arguments=arguments,
            idempotency_key=idempotency_key,
            correlation_id=f"{context.correlation_id}:{tool_name}",
        )

    def server(self, session: AsyncSession, *, context: MCPExecutionContext) -> FastMCP:
        """Create a run-scoped FastMCP server with no identity fields in its wire schema."""

        server = FastMCP(
            "agents-should-survive-failure-governed-tools",
            instructions=(
                "Calls are governed by the platform policy for the bound run and agent. "
                "The email tool is synthetic and cannot send real email."
            ),
            json_response=True,
        )

        @server.tool(name="vendor.lookup")
        async def vendor_lookup(  # pyright: ignore[reportUnusedFunction]
            external_reference: Annotated[str, Field(pattern=r"^[A-Za-z0-9._:-]{1,120}$")],
            idempotency_key: Annotated[
                str, Field(min_length=1, max_length=240, pattern=r"^[A-Za-z0-9._:-]+$")
            ],
        ) -> VendorLookupOutput:
            result = await self.call(
                session,
                context=context,
                tool_name="vendor.lookup",
                arguments={"external_reference": external_reference},
                idempotency_key=idempotency_key,
            )
            return VendorLookupOutput.model_validate(result.result)

        @server.tool(name="policy.search")
        async def policy_search(  # pyright: ignore[reportUnusedFunction]
            query: Annotated[str, Field(min_length=1, max_length=500)],
            idempotency_key: Annotated[
                str, Field(min_length=1, max_length=240, pattern=r"^[A-Za-z0-9._:-]+$")
            ],
            limit: Annotated[int, Field(ge=1, le=10)] = 5,
        ) -> PolicySearchOutput:
            result = await self.call(
                session,
                context=context,
                tool_name="policy.search",
                arguments={"query": query, "limit": limit},
                idempotency_key=idempotency_key,
            )
            return PolicySearchOutput.model_validate(result.result)

        @server.tool(name="email.send")
        async def email_send(  # pyright: ignore[reportUnusedFunction]
            recipient: Annotated[
                str, Field(min_length=3, max_length=320, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
            ],
            subject: Annotated[str, Field(min_length=1, max_length=240)],
            body: Annotated[str, Field(min_length=1, max_length=20_000)],
            idempotency_key: Annotated[
                str, Field(min_length=1, max_length=240, pattern=r"^[A-Za-z0-9._:-]+$")
            ],
        ) -> SyntheticEmailOutput:
            result = await self.call(
                session,
                context=context,
                tool_name="email.send",
                arguments={
                    "recipient": recipient,
                    "subject": subject,
                    "body": body,
                },
                idempotency_key=idempotency_key,
            )
            return SyntheticEmailOutput.model_validate(result.result)

        return server
