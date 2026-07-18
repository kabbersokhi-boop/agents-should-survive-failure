# Documentation

## Start here

- [Plain-English guide: who uses this, why vendor onboarding exists, and what the SDK does](plain-english-guide.md)
- [Five-to-ten-minute employer demonstration guide](demo.md)
- [Local development runbook](runbooks/local-development.md)
- [`v0.2.0` release evidence](evidence/v0.2.0.md)

## Understand the system

- [System boundaries and architecture decisions](adr/0001-system-boundaries.md)
- [Durable vendor onboarding](adr/0004-durable-vendor-onboarding-workflow.md)
- [Failure cases](failure-cases.md)
- [Threat model](threat-model.md)

## Run and demonstrate the system

- [Local development runbook](runbooks/local-development.md)
- [Employer demonstration guide](demo.md)
- [Plain-English public story](plain-english-guide.md)

## Review evidence

- [`v0.2.0` release evidence](evidence/v0.2.0.md)
- [Evaluation methodology](evaluation-methodology.md)
- [Reviewed evaluation cases](evaluation-cases-v1.md)
- [GitHub release assets](https://github.com/kabbersokhi-boop/agents-should-survive-failure/releases/tag/v0.2.0)

## Build an external agent

- [SDK package](../packages/agents-should-survive-failure-sdk/README.md)
- [Operations Investigation Agent](../packages/example-operations-agent/README.md)
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

- [Archived development log](archive/development-log.md)
- [Architecture decision records](adr/)
