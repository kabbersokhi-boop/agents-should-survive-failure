"""Discovery of trusted, operator-installed managed-agent distributions.

Only packages installed by the platform operator are considered. Discovery never
downloads code and registration remains data-only; loading an entry point is
reserved for worker execution after the installation has been selected.
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib.metadata import EntryPoint, entry_points
from typing import Any

from agents_should_survive_failure_sdk import ManagedAgent

from agents_should_survive_failure.agent_registry import AgentRegistration, parse_registration

MANAGED_AGENT_ENTRY_POINT_GROUP = "agents_should_survive_failure.agents"


class AgentDiscoveryError(ValueError):
    """An installed managed-agent package does not satisfy the trusted plugin contract."""


@dataclass(frozen=True)
class DiscoveredAgent:
    """A validated installed managed agent and its immutable registration declaration."""

    registration: AgentRegistration
    entry_point_name: str
    agent: ManagedAgent


def _distribution_name(entry_point: EntryPoint) -> str:
    distribution = entry_point.dist
    if distribution is None:
        raise AgentDiscoveryError("installed managed agent has no distribution metadata")
    name = distribution.metadata.get("Name")
    if not name:
        raise AgentDiscoveryError("installed managed agent distribution has no name")
    return name


def _group_entry_points() -> tuple[EntryPoint, ...]:
    """Return the standard entry-point group in a deterministic order."""

    points = entry_points(group=MANAGED_AGENT_ENTRY_POINT_GROUP)
    return tuple(sorted(points, key=lambda item: item.name))


def _load(entry_point: EntryPoint) -> ManagedAgent:
    try:
        candidate: Any = entry_point.load()
    except Exception as error:
        message = f"could not load managed-agent entry point {entry_point.name}"
        raise AgentDiscoveryError(message) from error
    agent = candidate() if isinstance(candidate, type) else candidate
    if not isinstance(agent, ManagedAgent):
        raise AgentDiscoveryError(
            f"managed-agent entry point {entry_point.name} does not implement ManagedAgent"
        )
    return agent


def discovered_agents() -> tuple[DiscoveredAgent, ...]:
    """Load and validate every operator-installed managed-agent entry point.

    This is intentionally explicit: an invalid installed plugin is an operator
    configuration error rather than a silently skipped agent version.
    """

    discovered: list[DiscoveredAgent] = []
    for entry_point in _group_entry_points():
        agent = _load(entry_point)
        registration = parse_registration(
            manifest=agent.metadata.model_dump(mode="json"),
            package_name=_distribution_name(entry_point),
            entry_point=entry_point.value,
        )
        discovered.append(
            DiscoveredAgent(
                registration=registration,
                entry_point_name=entry_point.name,
                agent=agent,
            )
        )
    return tuple(discovered)


def load_installed_agent(*, package_name: str, entry_point: str) -> ManagedAgent:
    """Load one previously registered agent only when it is a discovered plugin.

    A persisted module path alone is never sufficient authority to import code.
    The package and entry point must match an installed entry point from the
    dedicated managed-agent group.
    """

    for discovered in discovered_agents():
        registration = discovered.registration
        if registration.package_name == package_name and registration.entry_point == entry_point:
            return discovered.agent
    raise AgentDiscoveryError("registered managed-agent entry point is not installed")
