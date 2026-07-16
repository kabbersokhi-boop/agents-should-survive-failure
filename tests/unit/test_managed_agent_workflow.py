"""Managed-agent workflow cancellation behavior."""

import pytest

from agents_should_survive_failure.workflows import managed_agent
from agents_should_survive_failure.workflows.contracts import ManagedAgentInput


@pytest.mark.asyncio
async def test_managed_agent_marks_cancelled_result_without_terminal_regression(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def execute_activity(*args: object, **kwargs: object) -> dict[str, object]:
        del args, kwargs
        return {"cancelled": True}

    monkeypatch.setattr(managed_agent.workflow, "execute_activity", execute_activity)
    instance = managed_agent.ManagedAgentWorkflow()

    result = await instance.run(ManagedAgentInput(run_id="run-1"))

    assert result is None
    assert instance.phase() == "cancelled"
