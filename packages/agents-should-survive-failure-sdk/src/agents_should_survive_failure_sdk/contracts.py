"""Stable, server-independent contracts for trusted managed agent packages."""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator


class Capability(StrEnum):
    """Platform capabilities an agent can request in its immutable manifest."""

    APPROVALS = "approvals"
    ARTIFACTS = "artifacts"
    CHECKPOINTS = "checkpoints"
    DELEGATION = "delegation"
    MODELS = "models"
    TOOLS = "tools"


class SDKError(Exception):
    """Base class for structured, safe SDK errors."""

    code = "sdk_error"


class CancellationRequested(SDKError):
    """Raised when an agent attempts work after durable cancellation."""

    code = "cancellation_requested"


class BudgetExceeded(SDKError):
    """Raised when a platform-enforced budget has been exhausted."""

    code = "budget_exceeded"


class CapabilityDenied(SDKError):
    """Raised when an agent calls an undeclared or ungranted platform service."""

    code = "capability_denied"


class ContractModel(BaseModel):
    """Strict immutable base for the public manifest surface."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class ToolDeclaration(ContractModel):
    """A governed tool and exact version required by an agent."""

    name: str = Field(min_length=1, max_length=120, pattern=r"^[a-z0-9_.-]+$")
    version: str = Field(min_length=1, max_length=40)
    permissions: tuple[str, ...] = ()
    approval_required: bool = False


class BudgetRequirements(ContractModel):
    """Immutable upper bounds enforced for one managed run."""

    max_runtime_seconds: int = Field(default=300, ge=1, le=86_400)
    max_steps: int = Field(default=100, ge=1, le=10_000)
    max_model_calls: int = Field(default=20, ge=0, le=10_000)
    max_tool_calls: int = Field(default=50, ge=0, le=10_000)
    max_input_tokens: int = Field(default=100_000, ge=0, le=10_000_000)
    max_output_tokens: int = Field(default=100_000, ge=0, le=10_000_000)
    max_estimated_cost_microunits: int = Field(default=0, ge=0)
    max_child_agents: int = Field(default=0, ge=0, le=100)
    max_delegation_depth: int = Field(default=0, ge=0, le=20)
    max_artifact_bytes: int = Field(default=1_000_000, ge=0, le=100_000_000)
    max_checkpoint_bytes: int = Field(default=1_000_000, ge=0, le=100_000_000)


class DelegationPolicy(ContractModel):
    """The child-agent authority boundary declared by a parent agent."""

    allowed_agent_slugs: tuple[str, ...] = ()
    max_fan_out: int = Field(default=0, ge=0, le=100)
    max_depth: int = Field(default=0, ge=0, le=20)


class AgentMetadata(ContractModel):
    """Immutable identity, compatibility, and capability manifest for one agent version."""

    slug: str = Field(min_length=3, max_length=160, pattern=r"^[a-z0-9-]+$")
    version: str = Field(min_length=1, max_length=40)
    display_name: str = Field(min_length=3, max_length=200)
    description: str = Field(min_length=10, max_length=2_000)
    input_schema: Mapping[str, JsonValue]
    output_schema: Mapping[str, JsonValue]
    required_capabilities: tuple[Capability, ...] = ()
    tools: tuple[ToolDeclaration, ...] = ()
    required_permissions: tuple[str, ...] = ()
    approval_required: bool = False
    checkpoint_supported: bool = False
    artifact_supported: bool = False
    budget_defaults: BudgetRequirements = Field(default_factory=BudgetRequirements)
    delegation_policy: DelegationPolicy = Field(default_factory=DelegationPolicy)
    compatibility: str = Field(default=">=1.0.0,<2.0.0", min_length=1, max_length=120)

    @field_validator("tools")
    @classmethod
    def unique_tool_names(cls, value: tuple[ToolDeclaration, ...]) -> tuple[ToolDeclaration, ...]:
        names = [tool.name for tool in value]
        if len(names) != len(set(names)):
            raise ValueError("tool declarations must have unique names")
        return value


class AgentTask(BaseModel):
    """Typed task payload received by a managed agent."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    input: Mapping[str, JsonValue]


class AgentArtifact(ContractModel):
    """Artifact content requested as part of a result."""

    name: str = Field(min_length=1, max_length=240)
    content_type: str = Field(min_length=3, max_length=120)
    content: bytes = Field(max_length=1_000_000)


class ArtifactReference(ContractModel):
    """Durable artifact metadata returned by the platform."""

    artifact_id: str
    digest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    content_type: str
    size_bytes: int = Field(ge=0)


class CheckpointReference(ContractModel):
    """Versioned durable checkpoint metadata returned by the platform."""

    name: str
    schema_version: str
    digest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class AgentResult(BaseModel):
    """Structured terminal output returned by a managed agent."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    output: Mapping[str, JsonValue]
    summary: str = Field(min_length=1, max_length=4_000)
    artifacts: tuple[AgentArtifact, ...] = ()


@runtime_checkable
class RunContext(Protocol):
    """Safe platform services available to an executing managed agent."""

    @property
    def run_id(self) -> str:
        """Return the durable platform run identifier."""
        ...

    @property
    def correlation_id(self) -> str:
        """Return the trace and audit correlation identifier."""
        ...

    async def emit_event(
        self, event_type: str, summary: str, payload: Mapping[str, JsonValue]
    ) -> None:
        """Persist bounded agent progress evidence."""
        ...

    async def call_tool(
        self, name: str, arguments: Mapping[str, JsonValue], *, idempotency_key: str
    ) -> Mapping[str, JsonValue]:
        """Call a run-pinned governed tool through the platform gateway."""
        ...

    async def request_approval(self, summary: str) -> bool:
        """Wait durably for an authorized approval decision."""
        ...

    async def save_checkpoint(
        self, name: str, schema_version: str, value: Mapping[str, JsonValue]
    ) -> CheckpointReference:
        """Save a validated, versioned checkpoint idempotently."""
        ...

    async def load_checkpoint(self, name: str) -> Mapping[str, JsonValue] | None:
        """Load the latest named checkpoint for this pinned agent version."""
        ...

    async def create_artifact(self, artifact: AgentArtifact) -> ArtifactReference:
        """Store a bounded artifact with platform-managed provenance and integrity."""
        ...

    async def read_artifact(self, artifact_id: str) -> AgentArtifact:
        """Read a run-owned artifact after the platform verifies its integrity and provenance."""
        ...

    async def remaining_budget(self) -> Mapping[str, int]:
        """Return the remaining deterministic budget counters."""
        ...

    async def check_cancelled(self) -> None:
        """Raise ``CancellationRequested`` when the durable run is cancelled."""
        ...

    async def delegate(
        self, agent_slug: str, task: AgentTask, *, budget: BudgetRequirements
    ) -> AgentResult:
        """Start an allowed child agent with attenuated authority and budget."""
        ...

    async def call_model(self, input: Mapping[str, JsonValue]) -> Mapping[str, JsonValue]:
        """Call an allowed platform model without receiving provider credentials."""
        ...


@runtime_checkable
class ManagedAgent(Protocol):
    """A trusted package entry point executable by the durable platform runtime."""

    metadata: AgentMetadata

    async def run(self, task: AgentTask, context: RunContext) -> AgentResult:
        """Execute one task using only the constrained ``RunContext`` services."""
        ...
