# Failure Cases

The platform treats PostgreSQL and Temporal as separate durable systems. A persisted run start can
be retried with a stable Temporal workflow ID; `WorkflowAlreadyStartedError` is reconciliation, not
a duplicate execution. Approval decisions require the exact pending request and version. Tool calls
are idempotent only when their canonical arguments match.

Release evidence covers controlled model/tool failures, ambiguous starts, duplicate effects, and
worker termination in the post-commit acknowledgement window. It does not prove safe interruption
of arbitrary long-running activity code or recovery from every possible crash point.

Known incomplete cases include remote MCP outage and authentication, sandbox resource enforcement
across all target hosts, hostile third-party agent code, and provider-specific live failure
behavior. Remote MCP servers and SSRF-prone user URL ingestion are not supported by the current
API; those surfaces require explicit threat-model and integration-test updates before they are
added.
