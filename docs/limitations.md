# Limitations

This is a backend reference implementation, not a production-ready agent platform. The current
reference workflow is vendor onboarding. Tool execution is deterministic and local. A run-scoped
MCP adapter exposes three governed local tools, but remote MCP trust, transport authentication, and
a hostile-code sandbox are not implemented. NVIDIA NIM access is manual and credential-gated.

The repository does not yet provide the independently installable SDK, external agent packages,
artifact/checkpoint APIs, budgets, secret broker, multi-worker proof, or the 20-case real-workflow
evaluator required by the master plan.
