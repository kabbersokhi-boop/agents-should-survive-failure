# Local development runbook

## Prerequisites

- Docker Engine with Compose support
- Python 3.12
- uv 0.11.16

## Start and inspect

```bash
make setup
make up
curl --fail http://127.0.0.1:8000/health/live
curl --fail http://127.0.0.1:8000/health/ready
```

Local interfaces:

| Service | URL |
| --- | --- |
| API and OpenAPI | `http://127.0.0.1:8000/docs` |
| Temporal UI | `http://127.0.0.1:8080` |
| Prometheus | `http://127.0.0.1:9090` |
| Grafana | `http://127.0.0.1:3000` |
| Tempo API | `http://127.0.0.1:3200` |

Grafana allows anonymous Viewer access in local development. It provisions Prometheus and Tempo
without credentials. This setting is not suitable for an exposed deployment.

Use `docker compose ps` for service state and `docker compose logs <service>` for startup errors.
Readiness returns dependency names and sanitized failure categories. Stop the environment with
`make down`; named volumes remain so local state survives restarts.

## Observability

Prometheus scrapes both `api:8000/metrics` and the internal worker endpoint `worker:9100`. The
provisioned Grafana dashboard, **System health**, contains API and worker scrape health plus workflow,
model, governed-tool, approval, sandbox, and active-run panels. Metrics use bounded route templates,
registered tool names/versions, providers, models, and outcome categories; they do not label raw IDs,
prompts, secrets, or tool arguments.

Tempo receives API, database, Temporal, activity, model, and governed-tool spans. The official
Temporal interceptor creates workflow/activity traces without instrumenting workflow code directly,
preserving Temporal determinism. Local telemetry is intentionally unauthenticated only inside this
Compose deployment; do not expose these ports in a shared environment without access controls.

## Database lifecycle

The API runs `alembic upgrade head` and idempotent seed loading before it starts accepting
requests. For a locally reachable database configured by `DATABASE_URL`, run lifecycle commands
directly:

```bash
make migrate
make seed
make downgrade
```

Create every schema change as an Alembic revision. Do not call SQLAlchemy `create_all` from the
application or tests. A downgrade is a development and verification operation; back up persistent
data before using it outside the isolated smoke project.

Downgrading revision `b3c4d5e6f7a8` removes release evaluation runs, results, and reviewed case rows
because the older schema cannot represent their suite provenance or immutable digests. Legacy
evaluation records are retained. Re-upgrading and running `make seed` restores the reviewed
catalog; it does not restore removed run history.

## Local API-key lifecycle

Create a local key only when a client needs to call the authenticated API. The plaintext value is
printed once by the command and is never stored by the platform. Supply an optional timezone-aware
ISO-8601 expiry such as `2026-12-31T00:00:00Z`.

```bash
API_KEY_BOOTSTRAP_EMAIL=developer@example.invalid \
API_KEY_BOOTSTRAP_NAME='Local Developer' \
API_KEY_BOOTSTRAP_SCOPES='runs:read,runs:write' \
API_KEY_BOOTSTRAP_EXPIRES_AT='2026-12-31T00:00:00Z' \
make bootstrap-api-key
```

Revoke one key using its safe identifier, or disable the principal to reject every key issued to
that user:

```bash
API_KEY_IDENTIFIER=identifier-from-bootstrap-output make revoke-api-key
API_KEY_BOOTSTRAP_EMAIL=developer@example.invalid make disable-principal
```

## Evaluation execution

Validate the persisted production-workflow evaluation catalog from the local operator command:

```bash
make up
EVALUATION_IDEMPOTENCY_KEY=release-2026-07-11 make evaluate
```

The key is required and makes a repeated invocation return the original persisted run rather than
creating duplicate evidence. It runs inside the local API container and prints the run ID; retrieve
its bounded result report at `GET /evaluation-runs/{evaluation_run_id}`. The command reconstructs
and hashes all 24 stored case contracts and exits nonzero on missing, unexpected, or drifted rows,
then executes the production workflow and records bounded behavior evidence.

## Isolated smoke gate

`make test-integration` starts the `agents-verify` Compose project. It performs a clean migration,
downgrades to base, upgrades again, seeds and runs the catalog evaluator, then downgrades one
revision, re-upgrades, and reseeds to verify the explicit evaluation data boundary. It also verifies seed
data, reviewed-case immutability with operational enablement, relational constraints, repository
CRUD, optimistic concurrency, pgvector similarity, dependency readiness, Prometheus scraping,
Grafana provisioning, Tempo trace ingestion, and Temporal UI. It then removes only that project's
containers and volumes. It can run independently of unit tests, but `make verify` includes it.
