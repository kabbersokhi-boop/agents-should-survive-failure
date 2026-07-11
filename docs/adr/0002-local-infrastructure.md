# ADR 0002: Local infrastructure topology

- Status: Accepted
- Date: 2026-07-11

## Context

The reference system needs a reproducible local environment that exercises real PostgreSQL,
pgvector, Temporal, metrics, traces, and dashboards. Temporal execution history and application
records have different ownership even when development resource constraints favor one database
server.

## Decision

Docker Compose runs PostgreSQL 17 with pgvector 0.8.1, Temporal Server 1.29.1 and UI 2.52.0,
Prometheus 3.13.1, Grafana 13.1.0, and Tempo 3.0.2. Versions are explicit. Temporal 1.29.1 is the
version pinned by its official Compose repository and is the newest verified `auto-setup` image
available when this decision was recorded; the Python SDK is 1.30.0.

One PostgreSQL process hosts separate `agents`, `temporal`, and `temporal_visibility` databases.
The application connects only as the `agents` role and owns no Temporal objects. Temporal remains
the sole owner of workflow execution history, while the application database owns business and
audit records. Temporal's development-only `auto-setup` role is a PostgreSQL bootstrap superuser;
production deployments must use separately scoped infrastructure credentials.

The API starts even when dependencies are unavailable. `/health/live` reports only process
liveness; `/health/ready` concurrently probes PostgreSQL and Temporal with bounded timeouts and
returns 503 on either failure. Error types may be returned, but connection strings and raw error
messages are never exposed.

OTLP spans are exported to Tempo. Prometheus scrapes `/metrics`, and Grafana provisions both data
sources and a minimal system-health dashboard. The worker in Phase 1 verifies Temporal health but
does not poll a task queue until Phase 3 introduces real workflows.

## Consequences

Local development is realistic but resource-intensive. The shared PostgreSQL process is not a
claim that production deployments should share failure domains. `auto-setup` is for development
only. The integration gate uses an isolated Compose project and destroys its own volumes.
