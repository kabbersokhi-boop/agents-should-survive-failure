#!/usr/bin/env bash
set -euo pipefail

sdk_wheel="$(find packages/agents-should-survive-failure-sdk/dist -maxdepth 1 -name '*.whl' -print -quit)"
agent_wheel="$(find packages/example-operations-agent/dist -maxdepth 1 -name '*.whl' -print -quit)"
test_dir="$(mktemp -d)"
trap 'rm -rf "$test_dir"' EXIT

test -n "$sdk_wheel"
test -n "$agent_wheel"
uv venv "$test_dir" --python 3.12 >/dev/null
uv pip install --python "$test_dir/bin/python" "$sdk_wheel" "$agent_wheel" >/dev/null
"$test_dir/bin/python" -c '
import asyncio
from importlib.metadata import entry_points

from agents_should_survive_failure_sdk import AgentArtifact

points = tuple(entry_points(group="agents_should_survive_failure.agents"))
assert len(points) == 1
agent = points[0].load()()
assert agent.metadata.slug == "operations-investigation"

class Context:
    run_id = "run-1"
    correlation_id = "correlation-1"
    async def emit_event(self, *args): pass
    async def call_tool(self, *args, **kwargs): return {"citations": ["policy-1"]}
    async def request_approval(self, *args): return True
    async def save_checkpoint(self, *args): return None
    async def load_checkpoint(self, *args): return None
    async def create_artifact(self, artifact: AgentArtifact): return None
    async def remaining_budget(self): return {}
    async def check_cancelled(self): return None
    async def delegate(self, *args, **kwargs): raise AssertionError("not used")
    async def call_model(self, *args): raise AssertionError("not used")

result = asyncio.run(agent.run(
    __import__("agents_should_survive_failure_sdk").AgentTask(
        input={"incident_id": "INC-1", "question": "retention policy"}
    ),
    Context(),
))
assert result.output["incident_id"] == "INC-1"
assert len(result.artifacts) == 1
print("External agent clean-install and entry-point check passed")
'
