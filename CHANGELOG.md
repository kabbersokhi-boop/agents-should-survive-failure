# Changelog

## Unreleased

- Added the high-value refund reference workflow on the shared durable execution engine.
- Added a Grafana cost dashboard and cost-reporting helper.
- Restored strict type-checking for the refund workflow and tool activities.
- Reframed the public documentation around executable crash-recovery evidence and explicit
  maturity levels.

## v0.2.0

This release presents the durable vendor-onboarding workflow as a verified reference project.

- Real 24-case production-workflow evaluator with committed evidence.
- Controlled fault injection and OS-level worker-crash/replacement proof.
- Exactly-once business-effect evidence for approval, projection, and synthetic email.
- Public SDK preview and independently packaged Operations Investigation Agent.
- Durable checkpoints, artifacts, budgets, governed tools, and run-pinned versions.
- Release artifacts, Gitleaks, dependency audit, and backend, SDK, and container SBOMs.
- Known limitations documented in [docs/limitations.md](docs/limitations.md).

## Earlier backend baseline

Earlier releases established the FastAPI control plane, PostgreSQL persistence, Temporal workflows, authenticated API contracts, governed tools, policy retrieval, approval updates, observability, local sandbox controls, and migration lifecycle checks. Historical implementation notes remain in the [archived development log](docs/archive/development-log.md).
