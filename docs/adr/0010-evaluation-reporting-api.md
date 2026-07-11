# 0010 Evaluation Reporting API

## Status

Accepted.

## Decision

`GET /evaluation-runs/{evaluation_run_id}` provides a read-only report of persisted evaluation
configuration and per-case outcome summaries. It returns no evaluation input payloads or internal
execution details.

## Consequences

Reviewers can inspect deterministic evaluation outcomes through the control-plane API while the
database remains the source of record.
