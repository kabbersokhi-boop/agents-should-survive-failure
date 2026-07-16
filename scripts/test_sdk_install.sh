#!/usr/bin/env bash
set -euo pipefail

package_dir="packages/agents-should-survive-failure-sdk"
wheel="$(find "$package_dir/dist" -maxdepth 1 -name '*.whl' -print -quit)"
test_dir="$(mktemp -d)"
trap 'rm -rf "$test_dir"' EXIT

test -n "$wheel"
uv venv --offline "$test_dir" --python 3.12 >/dev/null
uv pip install --offline --python "$test_dir/bin/python" "$wheel" >/dev/null
"$test_dir/bin/python" -c '
from agents_should_survive_failure_sdk import AgentMetadata, AgentResult, AgentTask, ManagedAgent

class CleanInstallAgent:
    metadata = AgentMetadata(
        slug="clean-install-agent",
        version="1.0.0",
        display_name="Clean Install Agent",
        description="Verifies the public SDK can be imported independently.",
        input_schema={"type": "object"},
        output_schema={"type": "object"},
    )

    async def run(self, task: AgentTask, context: object) -> AgentResult:
        return AgentResult(output=task.input, summary="ok")

assert isinstance(CleanInstallAgent(), ManagedAgent)
print("SDK clean-install check passed")
'
