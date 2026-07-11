# Failure Cases

The platform treats PostgreSQL and Temporal as separate durable systems. A persisted run start can
be retried with a stable Temporal workflow ID; `WorkflowAlreadyStartedError` is reconciliation, not
a duplicate execution. Approval decisions require the exact pending request and version. Tool calls
are idempotent only when their canonical arguments match.

Known incomplete cases: worker termination during a long-running activity, MCP outage, sandbox
failure, real evaluator workflow execution, and provider failure injection are not yet proven.
