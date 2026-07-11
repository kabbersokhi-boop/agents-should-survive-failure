# Threat Model

## Assets

Protected assets include API keys, provider credentials, approval authority, workflow and business
records, tool results, and audit evidence. Private model reasoning is not an application asset and
is never intentionally persisted.

## Actors and Boundaries

API-key principals cross the public control-plane boundary. Workers cross the Temporal boundary.
Tools cross an agent-to-platform policy boundary; a tool receives only its scoped request and the
gateway, not an agent-provided permission set. PostgreSQL and Temporal are separate durable
systems, so workflow start and approval transitions require explicit recovery and idempotency.

## Primary Abuse Cases

- Credential theft, replay, expiration bypass, and scope escalation.
- Conflicting or stale approval decisions.
- Duplicate run, tool, or business side effects after retries.
- Tool argument substitution, version substitution, or self-granted permissions.
- Provider, Temporal, database, or worker failure during a durable transition.
- Untrusted tool/MCP/sandbox code reading host files, secrets, or network resources.

## Current Mitigations and Residual Risk

API keys are salted and hashed; scope and principal status are checked at the API boundary.
Approval and workflow-start operations use persisted idempotency, version checks, and audit data.
Tool permission derives from registered agent configuration and tool definitions. Gitleaks scans
Git-tracked content without a `.env` exemption.

MCP, sandbox isolation, full telemetry coverage, artifact/checkpoint isolation, and the SDK are
not yet implemented. They remain explicit residual risks and must not be represented as available
security controls.
