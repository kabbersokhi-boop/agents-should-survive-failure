"""Trusted installed managed-agent discovery boundary."""

from typing import Any, cast

import pytest
from agents_should_survive_failure_sdk import AgentMetadata, AgentResult, AgentTask

from agents_should_survive_failure import agent_discovery
from agents_should_survive_failure.agent_discovery import (
    AgentDiscoveryError,
    DiscoveredAgent,
    discovered_agents,
    load_installed_agent,
)
from agents_should_survive_failure.agent_registry import parse_registration


class InstalledAgent:
    metadata = AgentMetadata(
        slug="installed-agent",
        version="1.0.0",
        display_name="Installed Agent",
        description="A trusted managed agent installed in the worker environment.",
        input_schema={"type": "object"},
        output_schema={"type": "object"},
    )

    async def run(self, task: AgentTask, context: object) -> AgentResult:
        del context
        return AgentResult(output=task.input, summary="ok")


class FakeEntryPoint:
    name = "installed"
    value = "installed_agent.module:InstalledAgent"


def test_discovery_validates_operator_installed_entry_point(monkeypatch: Any) -> None:
    entry_point = FakeEntryPoint()

    def load(_: Any) -> InstalledAgent:
        return InstalledAgent()

    def distribution_name(_: Any) -> str:
        return "installed-agent-package"

    monkeypatch.setattr(agent_discovery, "_group_entry_points", lambda: (cast(Any, entry_point),))
    monkeypatch.setattr(agent_discovery, "_load", load)
    monkeypatch.setattr(agent_discovery, "_distribution_name", distribution_name)

    found = discovered_agents()

    assert len(found) == 1
    assert found[0].registration.package_name == "installed-agent-package"
    assert found[0].registration.entry_point == entry_point.value


def test_runtime_only_loads_registered_entry_points(monkeypatch: Any) -> None:
    registration = parse_registration(
        manifest=InstalledAgent.metadata.model_dump(mode="json"),
        package_name="installed-agent-package",
        entry_point="installed_agent.module:InstalledAgent",
    )
    monkeypatch.setattr(
        agent_discovery,
        "discovered_agents",
        lambda: (
            DiscoveredAgent(
                registration=registration,
                entry_point_name="installed",
                agent=InstalledAgent(),
            ),
        ),
    )

    loaded = load_installed_agent(
        package_name="installed-agent-package",
        entry_point="installed_agent.module:InstalledAgent",
    )

    assert loaded.metadata.slug == "installed-agent"
    with pytest.raises(AgentDiscoveryError, match="not installed"):
        load_installed_agent(package_name="other-package", entry_point="other.module:Agent")
