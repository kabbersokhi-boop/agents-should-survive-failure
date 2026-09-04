# Documentation

## Start here

- [System overview: users, workflow purpose, and SDK boundary](system-overview.md)
- [Technical walkthrough](technical-walkthrough.md)
- [Local development runbook](runbooks/local-development.md)
- [`v0.2.0` release evidence](evidence/v0.2.0.md)

## Understand the system

- [System boundaries and architecture decisions](adr/0001-system-boundaries.md)
- [Durable vendor onboarding](adr/0004-durable-vendor-onboarding-workflow.md)
- [Failure cases](failure-cases.md)
- [Threat model](threat-model.md)

## Run and demonstrate the system

- [Local development runbook](runbooks/local-development.md)
- [Technical walkthrough](technical-walkthrough.md)
- [System overview](system-overview.md)

## Review evidence

- [`v0.2.0` release evidence](evidence/v0.2.0.md)
- [Evaluation methodology](evaluation-methodology.md)
- [Reviewed evaluation cases](evaluation-cases-v1.md)
- [GitHub release assets](https://github.com/kabbersokhi-boop/agents-should-survive-failure/releases/tag/v0.2.0)

## Build an external agent

- [SDK package](../packages/agents-should-survive-failure-sdk/README.md)
- [Reference Operations Agent](../packages/reference-operations-agent/README.md)
- [Tool and agent trust model](security/tool-and-agent-trust.md)

The SDK and managed-agent runtime are preview surfaces. Vendor onboarding is the mature,
release-proven workflow. The high-value refund workflow is implemented on `main` and is awaiting
the next release evidence bundle.

## Operate and secure it

- [MCP boundary](security/mcp.md)
- [Sandbox boundary](security/sandbox.md)
- [Limitations](limitations.md)
- [Security reporting](../SECURITY.md)

## Historical material

- [Architecture decision records](adr/)
