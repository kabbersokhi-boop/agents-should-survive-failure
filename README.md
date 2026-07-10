# Agents Should Survive Failure

A durable, observable and secure control plane for long-running AI agents.

This repository is a production-style reference implementation for recoverable, permission-
controlled business workflows. Its first workflow will onboard synthetic vendors, combine
deterministic risk rules with cited model explanations, pause for human approval, and preserve a
complete audit trail across worker failures. It is an engineering demonstration, not a real
compliance product and not production ready.

## Current status

Phase 1 provides a verified local backend platform: dependency-aware FastAPI health checks,
PostgreSQL with pgvector, Temporal and its UI, an initial worker process, Prometheus, Grafana, and
Tempo. Application persistence and workflow logic begin in the next phases. See
[PROGRESS.md](PROGRESS.md) for verified progress and limitations.

## Quick start

Prerequisites: Python 3.12, [uv](https://docs.astral.sh/uv/), Docker, and Docker Compose.

```bash
make setup
make verify
make up
curl http://127.0.0.1:8000/health/live
curl http://127.0.0.1:8000/health/ready
```

`make up` builds and starts the local platform. The API is at port 8000, Temporal UI at 8080,
Prometheus at 9090, Grafana at 3000, and Tempo at 3200. `make down` stops it without deleting local
volumes. See the [local development runbook](docs/runbooks/local-development.md).

## NVIDIA configuration

Later phases will use NVIDIA NIM through a provider-independent interface. Copy the safe names
from `.env.example` into an untracked `.env` and set `NVIDIA_API_KEY`, `NVIDIA_BASE_URL`, and
`NVIDIA_MODEL`. The planned demonstration model is `z-ai/glm-5.2` through NVIDIA's OpenAI-
compatible API. CI never uses a live key and never silently falls back to another live provider.

## Engineering constraints

- Deterministic code owns validation, scoring, authorization, transitions, retries, budgets, and
  final writes.
- Models interpret and explain evidence but cannot authorize consequential actions.
- Temporal execution history and PostgreSQL application records have separate ownership.
- No private reasoning or chain-of-thought is stored or exposed.

## Developer commands

Run `make help` for the current command set. `make verify` is the complete local Phase 1 gate:
format and lint checks, strict type checking, unit tests with at least 80% coverage, Compose
validation, a redacted Gitleaks scan, and an isolated live infrastructure smoke test.

## License

Licensed under the Apache License 2.0. See [LICENSE](LICENSE).
