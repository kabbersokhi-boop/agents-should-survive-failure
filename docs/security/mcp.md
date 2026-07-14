# MCP Trust Boundary

The platform uses `mcp==1.28.1`, from the stable v1 release line of the official MCP Python SDK.
The current adapter is a local, run-scoped FastMCP server created by the managed execution host.
It exposes only these typed tools:

- `vendor.lookup` maps to `vendor_database_query@1`.
- `policy.search` maps to `internal_policy_search@1`.
- `email.send` maps to `synthetic_email_send@1`.

The execution host supplies the run ID, agent ID, and correlation ID when it creates the adapter.
MCP tool arguments include neither identity nor permissions. The gateway derives permissions from
the immutable platform policy for the registered agent version, validates inputs and outputs, records attempts, creates a
durable per-run binding to the first accepted tool definition version, and enforces email approval
before execution. A later request for another version of the same logical tool is denied and
recorded as a version mismatch.

The current implementation does not expose a network listener for this adapter. Remote MCP servers
are therefore unsupported, rather than implicitly trusted. Before adding one, the platform needs
mutual authentication, explicit server/tool version pinning, a bounded request/response policy,
safe retry semantics, and a transport-specific threat review. Docker sandboxing is a separate
capability and is not implied by MCP.
