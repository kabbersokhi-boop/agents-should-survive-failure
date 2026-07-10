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

## Isolated smoke gate

`make test-integration` starts the `agents-verify` Compose project, verifies dependency readiness,
Prometheus scraping, Grafana provisioning, Tempo trace ingestion, Temporal UI, and pgvector, then
removes only that project's containers and volumes. It can run independently of unit tests, but
`make verify` includes it.
