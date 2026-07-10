# Agents Should Survive Failure

A durable, observable and secure control plane for long-running AI agents.

This repository is a production-style reference implementation for recoverable, permission-
controlled business workflows. Its first workflow will onboard synthetic vendors, combine
deterministic risk rules with cited model explanations, pause for human approval, and preserve a
complete audit trail across worker failures. It is an engineering demonstration, not a real
compliance product and not production ready.

## Current status

Phase 0 establishes the typed Python project, a minimal liveness endpoint, local container
skeleton, security scanning, and CI. Application workflow logic begins only after this gate is
green. See [PROGRESS.md](PROGRESS.md) for verified progress and limitations.

## Quick start

Prerequisites: Python 3.12, [uv](https://docs.astral.sh/uv/), Docker, and Docker Compose.

```bash
make setup
make verify
make dev
curl http://127.0.0.1:8000/health/live
```

`make up` builds and starts the Phase 0 API and PostgreSQL skeleton; `make down` stops it.

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

Run `make help` for the current command set. `make verify` is the complete local Phase 0 gate:
format and lint checks, strict type checking, tests with at least 80% coverage, Compose validation,
and a redacted Gitleaks scan. Phase-specific commands will be added with their implementations.

## License

Licensed under the Apache License 2.0. See [LICENSE](LICENSE).
