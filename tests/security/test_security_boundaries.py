"""Adversarial checks for the security boundaries implemented in this release."""

from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from agents_should_survive_failure.api import ApprovalRequestBody, VendorCreateRequest
from agents_should_survive_failure.auth import (
    AuthenticatedPrincipal,
    generate_api_key,
    verify_secret,
)
from agents_should_survive_failure.mcp_adapter import GovernedMCPAdapter, MCPExecutionContext
from agents_should_survive_failure.policy import agent_tool_permissions
from agents_should_survive_failure.providers import ProviderError, classify_provider_error
from agents_should_survive_failure.sandbox import (
    DockerSandbox,
    SandboxPolicy,
    SandboxPolicyError,
    SandboxRequest,
)
from agents_should_survive_failure.tool_gateway import (
    ToolDeniedError,
    VendorLookupInput,
)


class DenyingGateway:
    async def invoke(self, session: AsyncSession, **kwargs: Any) -> object:
        del session, kwargs
        raise ToolDeniedError("tool invocation is not permitted")


@pytest.mark.security
def test_public_input_models_reject_injection_and_identity_override_fields() -> None:
    with pytest.raises(ValidationError):
        VendorCreateRequest.model_validate(
            {
                "external_reference": "V-100'; DROP TABLE vendors; --",
                "legal_name": "Synthetic Vendor",
                "jurisdiction": "US",
                "contact_email": "vendor@example.invalid",
            }
        )
    with pytest.raises(ValidationError):
        ApprovalRequestBody.model_validate(
            {
                "approval_request_id": str(uuid4()),
                "expected_version": 1,
                "decision": "approved",
                "rationale": "approve",
                "idempotency_key": "decision-1",
                "decided_by_id": str(uuid4()),
            }
        )


@pytest.mark.security
def test_tool_inputs_reject_permission_escalation_and_malformed_arguments() -> None:
    with pytest.raises(ValidationError):
        VendorLookupInput.model_validate({"external_reference": "V-100", "permissions": ["admin"]})
    with pytest.raises(ValidationError):
        VendorLookupInput.model_validate({"external_reference": "../../etc/passwd"})


@pytest.mark.security
@pytest.mark.asyncio
async def test_mcp_keeps_identity_out_of_wire_arguments_and_denies_policy_bypass() -> None:
    adapter = GovernedMCPAdapter(cast(Any, DenyingGateway()))
    context = MCPExecutionContext("run-a", "agent-a", "correlation-a")

    with pytest.raises(ToolDeniedError):
        await adapter.call(
            cast(AsyncSession, SimpleNamespace()),
            context=context,
            tool_name="vendor.lookup",
            arguments={"external_reference": "V-100", "agent_id": "agent-b"},
            idempotency_key="lookup-a",
        )

    server = adapter.server(cast(AsyncSession, SimpleNamespace()), context=context)
    schemas = [str(tool.inputSchema) for tool in await server.list_tools()]
    assert all("agent_id" not in schema and "permissions" not in schema for schema in schemas)


@pytest.mark.security
def test_sandbox_does_not_mount_host_paths_or_accept_unapproved_environment() -> None:
    sandbox = DockerSandbox(SandboxPolicy(environment_allowlist=frozenset({"SAFE"})))
    command = sandbox.build_command(
        SandboxRequest(command=("python", "-c", "print('safe')"), environment={"SAFE": "1"}),
        workspace=Path("/tmp/dedicated-workspace"),
        container_name="survive-security-test",
    )

    assert "/var/run/docker.sock" not in " ".join(command)
    assert "type=bind,source=/tmp/dedicated-workspace,target=/workspace" in command
    with pytest.raises(SandboxPolicyError):
        sandbox.build_command(
            SandboxRequest(command=("python", "-V"), environment={"API_KEY": "forbidden"}),
            workspace=Path("/tmp/dedicated-workspace"),
            container_name="survive-security-test",
        )


@pytest.mark.security
def test_credentials_and_provider_transport_errors_do_not_echo_secret_material() -> None:
    generated = generate_api_key()
    assert verify_secret(generated.plaintext.split(".", 1)[1], generated.secret_hash)
    assert generated.plaintext not in generated.secret_hash

    secret = "never-include-this-secret"
    error = classify_provider_error(OSError(f"connection failed with {secret}"))
    assert isinstance(error, ProviderError)
    assert secret not in str(error)


@pytest.mark.security
def test_admin_is_explicit_and_non_admin_scope_cannot_escalate() -> None:
    principal = AuthenticatedPrincipal(uuid4(), uuid4(), frozenset({"runs:read"}))
    assert not principal.allows("approvals:decide")
    assert not principal.allows("admin")


@pytest.mark.security
def test_unregistered_agent_version_cannot_self_grant_tool_permissions() -> None:
    assert agent_tool_permissions(name="vendor-onboarding", version="2") == frozenset()
    assert agent_tool_permissions(name="unregistered-agent", version="1") == frozenset()
