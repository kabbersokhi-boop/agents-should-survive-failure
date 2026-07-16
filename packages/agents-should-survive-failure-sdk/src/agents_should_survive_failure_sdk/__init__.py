"""Public contracts for implementing trusted managed agents."""

from agents_should_survive_failure_sdk.contracts import (
    AgentArtifact,
    AgentMetadata,
    AgentResult,
    AgentTask,
    ArtifactReference,
    BudgetRequirements,
    BudgetExceeded,
    Capability,
    CapabilityDenied,
    CancellationRequested,
    CheckpointReference,
    DelegationPolicy,
    ManagedAgent,
    RunContext,
    SDKError,
    ToolDeclaration,
)

__version__ = "1.0.0"

__all__ = [
    "AgentArtifact",
    "AgentMetadata",
    "AgentResult",
    "AgentTask",
    "ArtifactReference",
    "BudgetRequirements",
    "BudgetExceeded",
    "Capability",
    "CapabilityDenied",
    "CancellationRequested",
    "CheckpointReference",
    "DelegationPolicy",
    "ManagedAgent",
    "RunContext",
    "SDKError",
    "ToolDeclaration",
]
