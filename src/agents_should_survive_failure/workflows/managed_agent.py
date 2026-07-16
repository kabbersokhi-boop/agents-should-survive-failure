"""Temporal workflow shell for public-SDK managed agents.

Agent code executes only in an activity. The workflow itself retains deterministic orchestration
and a durable cancellation signal; database state remains the authoritative run snapshot.
"""

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from agents_should_survive_failure.workflows.contracts import ManagedAgentInput


MANAGED_AGENT_RETRY = RetryPolicy(initial_interval=timedelta(seconds=1), maximum_attempts=3)


@workflow.defn
class ManagedAgentWorkflow:
    """Execute one registered agent version through the durable managed-agent activity boundary."""

    def __init__(self) -> None:
        self._phase = "created"
        self._cancelled = False

    @workflow.run
    async def run(self, input: ManagedAgentInput) -> dict[str, object] | None:
        if self._cancelled:
            self._phase = "cancelled"
            return None
        self._phase = "executing"
        result = await workflow.execute_activity(
            "managed_agent.execute",
            input,
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=MANAGED_AGENT_RETRY,
        )
        if result.get("cancelled") is True:
            self._phase = "cancelled"
            return None
        self._phase = "completed"
        return result

    @workflow.signal
    def cancel(self) -> None:
        self._cancelled = True

    @workflow.query
    def phase(self) -> str:
        return self._phase
