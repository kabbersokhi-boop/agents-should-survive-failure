# 0008 Read-Only Workflow Evidence API

## Status

Accepted.

## Context

Durable events and model-call records are useful only when a reviewer can inspect them without
direct database access. The control plane needs a stable, read-only evidence contract.

## Decision

`GET /workflow-runs/{run_id}/evidence` returns ordered workflow events and bounded model-call
metadata for one workflow run. Event payloads carry policy citation provenance. Model-call records
include provider, model, status, usage, latency, failure category, and explanation summary.
Prompts and private reasoning are excluded.

## Consequences

Reviewers can inspect workflow provenance through the API while PostgreSQL remains the system of
record. The endpoint is read-only and returns a not-found response for unknown workflow runs.
