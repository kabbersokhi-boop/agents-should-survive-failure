# Backend Readiness Review: 2026-07

## Scope and Evidence

This review covers the checked-out `main` revision `5c7d062` on 2026-07-12. The required local
baseline commands passed: dependency sync, linting, type checking, unit tests (41 tests, 82%
coverage), Compose configuration validation, secret scan, and the 10-test isolated Compose
integration gate. The normal stack was restored after the isolated gate and readiness reported
healthy PostgreSQL and Temporal dependencies. Migration head is `4d902f85f639`.

Fetching GitHub `main` could not be independently reconfirmed because this environment could not
resolve `github.com`; this review therefore treats the checked-out local `main` as its source of
truth. It does not claim current remote CI status.

## Current Architecture

FastAPI exposes a vendor-onboarding control plane. Temporal owns durable workflow progress;
PostgreSQL/pgvector stores vendors, workflow records, policy documents, approval records, model
evidence, audits, and evaluations. A worker executes the vendor-onboarding workflow. Model and
embedding providers have deterministic and NVIDIA NIM adapters. Compose provides PostgreSQL,
Temporal, Tempo, Prometheus, and Grafana for local development.

## Reproduced Behavior

The live local flow created a synthetic vendor, started onboarding, waited for a human decision,
returned policy and model evidence, accepted an approval, and reached completion. The evidence API
returned ordered events and bounded model-call metadata. The exercised workflow run was
`0056740d-84a2-4c11-a946-7f4c4608e78c`.

## Confirmed Defects and Mismatches

- API routes are unversioned and no API-key authentication or scope enforcement exists.
- Approval requests accept a request-body identity default and use a signal without proving that
  the workflow is currently at a valid approval boundary.
- Run persistence and `TemporalClient.start_workflow` occur in separate operations, leaving a
  recoverable-start gap if the second operation fails.
- The tool gateway is a hard-coded vendor lookup, accepts caller-provided permissions, does not
  persist denied attempts, and is not the reference workflow's universal tool path.
- MCP and sandbox capabilities do not exist.
- The evaluation runner duplicates a jurisdiction rule rather than executing the real workflow;
  the seed contains one evaluation case.
- The worker-restart test resumes a workflow after it is already waiting, rather than during an
  activity with an intermediate effect.
- Tracing and metrics cover only a subset of requested boundaries; Grafana is a basic local health
  view rather than a workflow, security, cost, and evaluation operational dashboard.
- Compose does not pass documented NVIDIA provider settings through to the API or worker.
- `settings.py` defaults `NVIDIA_MODEL` to `z-ai/glm-5.2`, while `.env.example` and README name
  `mistralai/mistral-medium-3.5-128b`.
- `.gitleaks.toml` allow-lists every `.env` path, which suppresses detection for an unsafe class of
  files even though `.env` is ignored by Git.
- CI lacks a single auditable release gate containing a real evaluation run, dependency
  vulnerability scan, package build check, and future SDK compatibility check.
- The threat model, failure-case record, limitations, evaluation methodology, tool/agent trust
  guidance, and full external-agent developer documentation are absent.

## Security Risks

Unauthenticated mutation routes permit any network caller to create vendors, start runs, signal
approvals, and cancel workflows. The control plane has no principal or scope boundary, and its
approval identity is not derived from an authenticated credential. Tool permissions are supplied
at the call boundary rather than derived from a policy-owned agent registration. No sandbox is
available for attached agent code. These are release blockers, not production-readiness claims.

## SDK Blockers

The runtime is vendor-onboarding-specific and lacks authenticated, versioned list/detail/event
contracts, generic agent registration, policy-owned tool invocation, checkpoints, artifacts,
budgets, cancellation contracts, capability negotiation, and a generic execution boundary. An SDK
must not be started until the backend release gate is satisfied.

## Baseline Commands

```bash
uv sync --frozen --all-groups
make lint
make typecheck
make test
make compose-check
make secret-scan
make test-integration
```

The isolated integration gate requires port 5432. When the normal local Compose stack is running,
stop it before `make test-integration`, then restore it afterward.
