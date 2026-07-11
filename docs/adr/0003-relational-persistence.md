# ADR 0003: Relational persistence and migration ownership

- Status: Accepted
- Date: 2026-07-11

## Context

Application records must remain queryable and auditable without duplicating Temporal's internal
execution state. The vendor workflow also needs database-enforced idempotency, approval integrity,
concurrent-update protection, and vector retrieval. Schema changes must be reproducible from an
empty database and reversible in CI.

## Decision

SQLAlchemy 2 asynchronous mappings define 17 application-owned tables. UUIDs identify records;
timezone-aware PostgreSQL timestamps record UTC instants; native enums constrain statuses; foreign
keys and uniqueness constraints enforce ownership and idempotency. JSONB is limited to flexible
configuration, inputs, summaries, evidence, and schemas. Audit and workflow event records are
append-oriented. Mutable vendors, workflow runs, and approval requests use SQLAlchemy version
columns for optimistic concurrency.

Alembic is the only schema creation mechanism. The initial revision creates the vector extension,
tables, enums, constraints, and indexes. Its downgrade removes every table and application enum so
an immediate re-upgrade succeeds. API startup upgrades to head before loading deterministic,
conflict-safe development seeds and before serving traffic.

Policy chunks use pgvector with an HNSW cosine index. Phase 2 uses eight-dimensional deterministic
embeddings solely to test the storage and query contract without an external API. Phase 5 will add
a migration to the selected production embedding provider's documented dimension.

Repositories accept a caller-owned `AsyncSession`; the service or activity boundary owns commit
and rollback. This permits a business operation and its audit event to commit atomically.

## Consequences

Database constraints provide a final line of defense beyond application validation. Callers must
handle uniqueness, foreign-key, check-constraint, and stale-version failures explicitly. Startup
migrations are appropriate for the single-instance local demonstration; a production deployment
must run migrations as a separate release job before scaling application instances.
