# Threat Model

## Assets

Protected assets include API keys, provider credentials, approval authority, workflow and business
records, tool results, and audit evidence. Private model reasoning is not an application asset and
is never intentionally persisted.

## Actors and Boundaries

API-key principals cross the public control-plane boundary. Workers cross the Temporal boundary.
Tools cross an agent-to-platform policy boundary; a tool receives only its scoped request and the
gateway, not an agent-provided permission set. The local MCP adapter is a run-scoped translation
layer over that same gateway, not a bypass. The Docker sandbox broker is privileged but does not
mount its Docker socket into a workload. PostgreSQL and Temporal are separate durable systems, so
workflow start and approval transitions require explicit recovery and idempotency.

## Primary Abuse Cases

- Credential theft, replay, expiration bypass, and scope escalation.
- Conflicting or stale approval decisions.
- Duplicate run, tool, or business side effects after retries.
- Tool argument substitution, version substitution, or self-granted permissions.
- Provider, Temporal, database, or worker failure during a durable transition.
- Untrusted tool/MCP/sandbox code reading host files, secrets, or network resources.
- Supply-chain compromise or a known-vulnerable application/container dependency.
- Prompt-injection text attempting to change deterministic policy, approval, or tool authority.

## Current Mitigations and Residual Risk

API keys are salted and hashed; scope and principal status are checked at the API boundary.
Approval and workflow-start operations use persisted idempotency, version checks, and audit data.
Tool permission derives from registered agent configuration and tool definitions. The MCP adapter
does not accept caller-supplied agent identity or permissions. The bounded sandbox uses a temporary
workspace, non-root execution, disabled networking, read-only root filesystem, dropped
capabilities, and resource limits. Gitleaks scans Git-tracked content without a `.env` exemption.
`pip-audit` scans exported locked production dependencies, and CycloneDX SBOMs are generated for
both the Python environment and local container image.

Remote MCP transport/authentication, artifact/checkpoint isolation, the SDK, external agent trust,
and hostile-code isolation remain incomplete. Docker is not presented as a complete hostile-code
boundary. The deterministic policy/approval path ignores model recommendations, but policy text is
not yet treated as an independently versioned, signed administrative input.
