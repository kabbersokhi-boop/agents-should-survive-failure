# Managed Agent SDK

`agents-should-survive-failure-sdk` is the server-independent contract between the workflow
runtime and an operator-installed agent package. It contains no database, Temporal, web, or model
provider dependency.

An agent declares immutable metadata and implements `ManagedAgent.run`. At run time, the platform
supplies a constrained `RunContext` with the evidence and tools granted to that run. The agent
cannot expand this grant.

## Install from this repository

```bash
python -m pip install ./packages/agents-should-survive-failure-sdk
```

## Implement the contract

```python
from agents_should_survive_failure_sdk import ManagedAgent, RunContext


class VendorReviewAgent(ManagedAgent):
    async def run(self, context: RunContext) -> None:
        # Use only evidence and operations exposed by this run context.
        ...
```

See the [reference operations agent](../reference-operations-agent/) for package discovery,
registration metadata, and a complete implementation. The SDK follows semantic versioning;
backward-incompatible changes to public types require a major release.
