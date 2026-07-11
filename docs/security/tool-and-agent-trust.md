# Tool and Agent Trust

Agents request tool calls; they do not supply the permission set that authorizes them. The governed
gateway resolves the registered agent configuration, tool version, input contract, idempotency key,
and risk metadata before executing a local handler. Tool definitions are persisted and versioned.

This is not a claim of sandboxing. A future MCP adapter and sandbox broker must remain behind this
gateway and preserve run identity, correlation, timeout, evidence, and audit data.
