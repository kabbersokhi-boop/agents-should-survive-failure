# Progress

## Current phase

Phase 5: provider contracts, NVIDIA NIM adapters, policy embedding migration, and model-call
evidence complete.

## Completed work

- Established Python 3.12 project metadata and a minimal typed liveness API.
- Added Ruff, Pyright, pytest, coverage, pre-commit, Gitleaks, CI, and Docker scaffolding.
- Recorded the initial system-boundary ADR.
- Added lifecycle-managed PostgreSQL and Temporal clients with fail-closed readiness.
- Added request IDs, JSON logs, Prometheus metrics, FastAPI tracing, and OTLP export to Tempo.
- Added PostgreSQL/pgvector, Temporal, Temporal UI, API, worker, Prometheus, Grafana, and Tempo.
- Provisioned Grafana data sources and a system-health dashboard.
- Added an isolated end-to-end infrastructure smoke test.
- Added all 17 required application tables with UUID keys, native status enums, foreign keys,
  useful indexes, uniqueness and idempotency constraints, and bounded JSONB use.
- Added optimistic concurrency for mutable vendor, run, and approval records.
- Added async repositories for vendors, workflow runs/events, and append-oriented audit events.
- Added a reversible initial Alembic revision and idempotent development seed data.
- Added an eight-dimensional deterministic test embedding and HNSW cosine index; production
  embedding dimensions will be introduced with the selected embedding provider in Phase 5.
- Added a Temporal vendor-onboarding workflow with deterministic risk assessment, durable human
  approval signals, status queries, cancellation, retries, and idempotent database activities.
- Replaced the connectivity-only worker with a real Temporal worker on the vendor-onboarding queue.
- Added API routes to create synthetic vendors, start idempotent runs, query workflow state, submit
  approval decisions, and cancel pending onboarding.
- Added deterministic pgvector policy retrieval with citations, a fail-closed read-only vendor lookup
  gateway with idempotent invocation evidence, and persisted deterministic evaluation results.
- Added explicit chat and embedding provider contracts with deterministic CI implementations and
  NVIDIA NIM adapters.
- Added durable model-call evidence for the vendor risk explanation; models remain unable to
  approve vendors or alter deterministic risk scores.
- Migrated policy vectors from the temporary 8-dimensional test format to NVIDIA's 2,048-
  dimensional embedding contract, with reversible HNSW-index migration and a reindex command.

## Commands and test results

- `uv lock`: 41 packages resolved with Python 3.12.13.
- `make lint`: Ruff formatting and lint checks passed.
- `make typecheck`: strict Pyright passed with zero errors.
- `make test`: one unit test passed with 100% branch coverage.
- `make compose-check`: Docker Compose configuration validated.
- `make secret-scan`: Gitleaks 8.28.0 scanned the repository with no leaks found.
- `make verify`: complete Phase 0 gate passed.
- `docker build -t agents-should-survive-failure:phase0 .`: non-root API image built.
- Phase 0 GitHub Actions run `29124716297`: passed.
- Phase 1 unit suite: 7 tests passed with 87% branch coverage.
- Phase 1 Compose smoke: 1 integration test passed against all eight services.
- Phase 1 `make verify`: passed, including trace ingestion and provisioned data-source checks.
- Phase 2 unit suite: 11 tests passed with 89% branch coverage.
- Phase 2 migration lifecycle: clean upgrade, downgrade to base, and re-upgrade passed.
- Phase 2 integration suite: 6 tests passed against PostgreSQL and the complete Compose stack.
- Phase 3 workflow integration suite: approval after worker restart and cancellation both passed.
- Phase 4 service integration suite: policy retrieval, permitted and denied tool paths, idempotency,
  and evaluation result persistence passed.
- Phase 5 local model checks: Mistral Medium 3.5 128B chat and Nemotron Embed 1B v2 embeddings
  both returned successful NVIDIA NIM responses.

## Architecture decisions

- Temporal owns workflow execution state; PostgreSQL owns application and audit data.
- Application code will depend on provider interfaces, never directly on NVIDIA APIs.
- CI will use deterministic providers and make no external model calls.
- Local PostgreSQL hosts separate application and Temporal databases with separate roles.
- The API starts independently of dependency availability; readiness probes PostgreSQL and Temporal.
- Official Tempo 3 single-binary configuration is used; legacy Tempo 2 blocks are invalid.
- Alembic is the only schema creation path; API startup migrates before serving and then loads
  deterministic seeds with conflict-safe inserts.
- PostgreSQL status transitions remain explicit enum values, while Temporal alone owns durable
  workflow execution history.
- Temporal workflows coordinate intent and waits; retry-safe activities own PostgreSQL transitions
  and append-only evidence.
- Tool calls require explicit permissions and write invocation evidence; policy retrieval preserves
  citations until the provider phase.

## Known limitations

- Compose credentials and anonymous Grafana access are development-only.
- Existing policy records need `make reindex-policies` after migration when `MODEL_PROVIDER=nvidia_nim`
  is enabled; the migration intentionally invalidates previous test vectors rather than treating
  them as semantically compatible.

## Next phase

The next phase is not yet defined in the repository roadmap.

## Manual action required

None.
