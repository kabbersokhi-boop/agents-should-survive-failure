# Reference Operations Agent

This independently installable package demonstrates the managed-agent contract without importing
the platform implementation. It depends only on `agents-should-survive-failure-sdk`.

## Install

Build and install the SDK first, then install this package:

```bash
python -m pip install ./packages/agents-should-survive-failure-sdk
python -m pip install ./packages/reference-operations-agent
```

The package publishes a standard Python entry point. The platform discovers the entry point,
validates its immutable metadata, and supplies a run-scoped context when it executes the agent.
Integration tests install the built wheels in an isolated environment and verify discovery.

## Trust boundary

This package is trusted, operator-installed code. It is not a sandbox for untrusted wheels. The
runtime limits the business operations exposed through `RunContext`, but Python code in the
worker process retains the operating-system authority of that process. See the
[tool and agent trust model](../../docs/security/tool-and-agent-trust.md).
