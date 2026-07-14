# Agents Should Survive Failure

A durable control plane for governed AI workflows.

This reference implementation demonstrates a vendor-onboarding workflow that combines durable
orchestration, policy retrieval, model explanations, human approval, and auditable state changes.
It is designed to make consequential workflow decisions deterministic while models interpret
bounded evidence.

## Capabilities

- Durable, restart-safe vendor onboarding through Temporal workflows, validated approval updates,
  and cancellation signals.
- PostgreSQL-backed application state, append-only audit evidence, and optimistic concurrency.
- Permissioned, idempotent tool invocation with explicit authorization boundaries.
- Policy retrieval with cited source material and 2,048-dimensional semantic embeddings.
- NVIDIA NIM adapters for Mistral Medium 3.5 128B explanations and Nemotron embeddings.
- Explicit model completion limits and bounded persisted explanation summaries.
- Structured logs, request IDs, Prometheus metrics, OpenTelemetry traces, and readiness checks.
- Read-only workflow evidence API for ordered state changes, policy citations, and model-call
  metadata.
- Read-only evaluation reports for persisted behavioral-contract outcomes.
- Explicit, idempotent local evaluation execution for release and operator workflows.
- Reproducible local infrastructure: API, worker, PostgreSQL/pgvector, Temporal, Grafana,
  Prometheus, and Tempo.

## Architecture

Temporal owns durable workflow execution. PostgreSQL owns domain state and evidence. Model and
embedding providers sit behind explicit contracts, allowing deterministic local verification and
NVIDIA NIM runtime integration without coupling workflow logic to a provider SDK. See the
[architecture decisions](docs/adr) for the design record.

## Quick start

Prerequisites: Python 3.12, [uv](https://docs.astral.sh/uv/), Docker, and Docker Compose.

```bash
make setup
make verify
make up
curl http://127.0.0.1:8000/health/live
curl http://127.0.0.1:8000/health/ready
```

`make up` builds and starts the local platform. API startup migrates the application database to
Alembic head and loads idempotent development seeds. The API is at port 8000, Temporal UI at 8080,
Prometheus at 9090, Grafana at 3000, and Tempo at 3200. `make down` stops it without deleting local
volumes. See the [local development runbook](docs/runbooks/local-development.md).

To exercise the workflow, create a vendor, start onboarding, then submit the human decision using
the returned IDs. `GET /workflow-runs/{run_id}` exposes the durable phase, and `DELETE` on that path
cancels a pending review. The API schema at `/docs` contains the precise request contracts.

Authenticated clients with `runs:read` can follow persisted run evidence through
`GET /api/v1/workflow-runs/{run_id}/events/stream`. The endpoint is Server-Sent Events with a
monotonic event sequence as its cursor: pass `after_sequence` for an initial replay or send the
standard `Last-Event-ID` header to resume. Event data contains only bounded persisted evidence, not
provider prompts, credentials, or private model reasoning.

Run the deterministic behavioral-contract suite against the local database with an explicit key:

```bash
make up
EVALUATION_IDEMPOTENCY_KEY=release-2026-07-11 make evaluate
```

The command returns an evaluation run ID. Inspect its bounded outcome report through
`GET /evaluation-runs/{evaluation_run_id}`. It exits nonzero when any behavior contract fails.

## Model Configuration

Copy the safe names from `.env.example` into an untracked `.env`. To use NVIDIA NIM locally, set
`MODEL_PROVIDER=nvidia_nim`, `NVIDIA_API_KEY`, `NVIDIA_BASE_URL`,
`NVIDIA_MODEL=mistralai/mistral-medium-3.5-128b`, and
`NVIDIA_EMBEDDING_MODEL=nvidia/llama-nemotron-embed-1b-v2`, plus an optional
`NVIDIA_TIMEOUT_SECONDS`. The worker then records only bounded
model summaries and usage data; it never grants approvals. Run `make reindex-policies` after a
migration to generate live policy embeddings. CI requires no credentials and uses the explicit
deterministic provider.

For a local credential check, set those variables in an untracked `.env`, then run
`make nim-smoke-test` and `make nim-embedding-smoke-test`. These manual commands do not run in
public pull requests and print metadata only, never the credential or model response body.

## Governance Boundaries

- Deterministic code owns validation, scoring, authorization, transitions, retries, budgets, and
  final writes.
- Models interpret and explain evidence but cannot authorize consequential actions.
- Temporal execution history and PostgreSQL application records have separate ownership.
- No private reasoning or chain-of-thought is stored or exposed.

## Verification

`make verify` runs formatting, static analysis, unit coverage, Compose validation, secret scanning,
reversible migrations, and integration checks. Use `make migrate`, `make downgrade`, and `make
seed` for database lifecycle operations outside Compose.

## License

Licensed under the Apache License 2.0. See [LICENSE](LICENSE).
