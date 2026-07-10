# Progress

## Current phase

Phase 1: local infrastructure complete. Phase 2 is next.

## Completed work

- Established Python 3.12 project metadata and a minimal typed liveness API.
- Added Ruff, Pyright, pytest, coverage, pre-commit, Gitleaks, CI, and Docker scaffolding.
- Recorded the initial system-boundary ADR.
- Added lifecycle-managed PostgreSQL and Temporal clients with fail-closed readiness.
- Added request IDs, JSON logs, Prometheus metrics, FastAPI tracing, and OTLP export to Tempo.
- Added PostgreSQL/pgvector, Temporal, Temporal UI, API, worker, Prometheus, Grafana, and Tempo.
- Provisioned Grafana data sources and a system-health dashboard.
- Added an isolated end-to-end infrastructure smoke test.

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

## Architecture decisions

- Temporal owns workflow execution state; PostgreSQL owns application and audit data.
- Application code will depend on provider interfaces, never directly on NVIDIA APIs.
- CI will use deterministic providers and make no external model calls.
- Local PostgreSQL hosts separate application and Temporal databases with separate roles.
- The API starts independently of dependency availability; readiness probes PostgreSQL and Temporal.
- Official Tempo 3 single-binary configuration is used; legacy Tempo 2 blocks are invalid.

## Known limitations

- No application schema or migrations exist until Phase 2.
- The Phase 1 worker checks Temporal connectivity but does not poll until Phase 3.
- Compose credentials and anonymous Grafana access are development-only.

## Next phase

Phase 2 database schema, Alembic migrations, repositories, and seed data.

## Manual action required

None.
