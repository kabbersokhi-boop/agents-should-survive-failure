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

## Evaluation execution

Run deterministic vendor-onboarding behavior evaluations only from the local operator command:

```bash
make up
EVALUATION_IDEMPOTENCY_KEY=release-2026-07-11 make evaluate
```

The key is required and makes a repeated invocation return the original persisted run rather than
creating duplicate evidence. It runs inside the local API container and prints the run ID; retrieve
its bounded result report at `GET /evaluation-runs/{evaluation_run_id}`.

## Isolated smoke gate

`make test-integration` starts the `agents-verify` Compose project. It performs a clean migration,
downgrades to base, upgrades again, and verifies seed data, relational constraints, repository
CRUD, optimistic concurrency, pgvector similarity, dependency readiness, Prometheus scraping,
Grafana provisioning, Tempo trace ingestion, and Temporal UI. It then removes only that project's
containers and volumes. It can run independently of unit tests, but `make verify` includes it.
