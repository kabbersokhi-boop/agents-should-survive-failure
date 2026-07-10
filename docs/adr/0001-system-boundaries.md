# ADR 0001: System boundaries and phased delivery

- Status: Accepted
- Date: 2026-07-11

## Context

Long-running agent execution combines durable orchestration, business records, model calls,
tools, and human decisions. Conflating these responsibilities makes recovery and audit claims
difficult to verify.

## Decision

Use a typed Python monorepo. Temporal will own durable execution history; PostgreSQL will own
business entities and queryable audit records. Provider, workflow, persistence, tool-gateway,
and policy boundaries will remain explicit. Phase 0 intentionally contains only a minimal
liveness component while it establishes the quality and delivery controls required by later
phases. Frontend work is prohibited until the backend release gate is proven and tagged.

Python 3.12 is pinned because it is mature and supported across the planned FastAPI, SQLAlchemy,
Temporal, OpenTelemetry, MCP, and testing stack.

## Consequences

Cross-boundary operations require explicit contracts and idempotency. The initial repository is
small, but later phases can add cohesive packages without changing the governing ownership model.
