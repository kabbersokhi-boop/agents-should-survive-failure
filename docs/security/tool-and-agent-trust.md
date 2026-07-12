# Tool and Agent Trust

Agents request tool calls; they do not supply the permission set that authorizes them. The governed
gateway resolves the registered agent configuration, tool version, input contract, idempotency key,
and risk metadata before executing a local handler. Tool definitions are persisted and versioned.

The local MCP adapter uses the stable v1 MCP Python SDK and exposes `vendor.lookup`,
`policy.search`, and `email.send`. A managed execution host constructs the adapter with a trusted
run, agent, and correlation context; those values are not fields that an MCP caller can provide.
Each MCP tool maps to a registered gateway tool version. The email tool stores a synthetic message
in PostgreSQL and has no SMTP or external delivery integration. Consequential email calls require
an approved decision for the same run.

The adapter is currently intended only for an in-process or authenticated local managed runner. It
is not mounted as an unauthenticated public MCP HTTP endpoint, and remote MCP servers are not yet
trusted or supported. A remote integration must authenticate both sides, pin server and tool
versions, propagate the trusted run context, and treat all responses as untrusted input before it
can be enabled.

This is not a claim of sandboxing. Any future sandbox broker must remain behind the same gateway
and preserve run identity, correlation, timeout, evidence, and audit data.
