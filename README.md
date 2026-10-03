# Agents Should Survive Failure

**A durable execution and governance reference for AI-assisted workflows. It survives worker
crashes, waits for human approval, and prevents duplicate business effects.**

[![CI](https://github.com/kabbersokhi-boop/agents-should-survive-failure/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/kabbersokhi-boop/agents-should-survive-failure/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/kabbersokhi-boop/agents-should-survive-failure)](https://github.com/kabbersokhi-boop/agents-should-survive-failure/releases/latest)
[![Python 3.12](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)](pyproject.toml)
[![License](https://img.shields.io/github/license/kabbersokhi-boop/agents-should-survive-failure)](LICENSE)

> **Core invariant:** execution can occur more than once; each business effect commits once.

## The business problem

A supplier approval is consequential work, not just a model response. A process can crash
after saving the decision but before acknowledging success. Blindly retrying can repeat the
action; abandoning the case can leave an approved supplier stranded in an unfinished workflow.

This project separates model advice, operator authority, durable coordination and committed
business effects. The reference supplier journey makes the difficult moment visible:
**the decision is saved, the worker dies, and recovery must preserve the same single outcome.**

## Failure proof

The reference workflow commits an approval, a vendor projection, and a synthetic notification.
The worker then receives `SIGKILL` before it can acknowledge completion to Temporal. A replacement
worker receives the activity again. Stable idempotency keys, database transactions, and PostgreSQL
uniqueness constraints make the replay converge on the committed effects.

```text
Worker crash proof passed: run=<uuid> decisions=1 projections=1 emails=1
retry=temporal-redelivery
```

Run the proof against real Temporal and PostgreSQL services:

```bash
make demo
```

This is at-least-once execution with exactly-once business effects for the tested workflow. It is
not a claim of exactly-once distributed execution.

## Watch the end-to-end demo

[![Supplier intake, observed worker crash and recovered case](docs/evidence/portfolio/demo/preview.gif)](https://raw.githubusercontent.com/kabbersokhi-boop/agents-should-survive-failure/57cd96e6554399c4002cc14fcb2320c0e50c4fad/docs/evidence/portfolio/demo/supplier-crash-recovery.mp4)

**[Watch the full demo](https://raw.githubusercontent.com/kabbersokhi-boop/agents-should-survive-failure/57cd96e6554399c4002cc14fcb2320c0e50c4fad/docs/evidence/portfolio/demo/supplier-crash-recovery.mp4)** —
2:57, captioned, 1080p, no audio.

1. **0:09 · Intake:** enter a fictional supplier through the authenticated operator console.
2. **0:29 · Review:** inspect deterministic risk, retrieved policy evidence and bounded model advice.
3. **0:56 · Approve:** submit a human decision against the exact approval-request version.
4. **1:06 · Interrupt:** commit the business effects, then observe `SIGKILL` before acknowledgement.
5. **1:48 · Recover:** inspect the replacement process and Temporal's redelivered activity.
6. **2:29 · Verify:** return to the same case: one decision, one approved supplier and one notification.

The footage uses real local Temporal and PostgreSQL services. The model provider is deterministic;
the notification is stored locally, not delivered email. The crash monitor is a read-only local
harness view, not a production application feature. Grafana provides aggregate operational
context; the selected case's database counts prove its business outcome.

![Versioned human approval and persisted policy evidence](docs/evidence/portfolio/demo/01-operator-review.png)

*An authenticated operator owns the decision. Policy evidence and model advice cannot approve it.*

![Completed Temporal execution and redelivered decision activity](docs/evidence/portfolio/demo/04-temporal-redelivery.png)

*Native Temporal history shows completion after redelivery for the same supplier case.*

See the [chapter-by-chapter walkthrough and verification](docs/portfolio-demo.md),
the [reproducible local guide](docs/operator-demo.md), and the
[earlier crash-recovery evidence](docs/evidence/portfolio/README.md).

## Engineering guarantees

| Guarantee | Mechanism | Executable evidence |
| --- | --- | --- |
| Durable execution | Temporal history, retries, updates, and replacement workers | [Crash proof](scripts/worker_crash_proof.py) |
| Duplicate-safe effects | Stable keys, SQL transactions, and unique constraints | [Integration tests](tests/integration/test_vendor_onboarding_workflow.py) |
| Recoverable starts | Persisted start intent and stable workflow identity | [Start coordinator](src/agents_should_survive_failure/workflow_starts.py) |
| Human authority | Scoped keys, versioned requests, and idempotent decisions | [Approval tests](tests/unit/test_approval_updates_api.py) |
| Governed tools | Run-scoped grants, pinned versions, schema validation, and audit | [Tool gateway](src/agents_should_survive_failure/tool_gateway.py) |
| Bounded model role | Deterministic risk policy; advisory model explanations | [Model evidence service](src/agents_should_survive_failure/model_evidence.py) |

## Architecture

```mermaid
flowchart LR
    C[Authenticated client] --> A[FastAPI control plane]
    A -->|persist start intent| P[(PostgreSQL)]
    A -->|start or update| T[Temporal]
    T --> W[Workflow worker]
    W --> G[Governed tool gateway]
    W --> M[Advisory model provider]
    W --> H[Human approval boundary]
    G -->|idempotent effects| P
    H -->|authenticated decision| A
    P --> E[Audit and evaluation evidence]
```

Authority stays separated by design:

1. Temporal owns durable coordination and redelivery.
2. PostgreSQL owns business truth, audit records, and idempotency constraints.
3. Deterministic policy owns risk calculation and authorization boundaries.
4. The model explains bounded evidence; it does not grant authority.
5. An authenticated human or service approves consequential decisions.

The same contracts support vendor onboarding and high-value refunds. Vendor onboarding is the
release-backed reference workflow. The refund workflow demonstrates reuse of the engine contracts
and remains a preview until its evaluation evidence is part of a release.

## Run locally

Requirements: Python 3.12, [uv](https://docs.astral.sh/uv/), Docker, and Docker Compose.

```bash
make setup
make up
curl --fail http://127.0.0.1:8000/health/ready
```

| Interface | Local URL | Purpose |
| --- | --- | --- |
| Operator console | `http://127.0.0.1:8000/console/` | Supplier intake, versioned approval and case-specific evidence |
| FastAPI / OpenAPI | `http://127.0.0.1:8000/docs` | Start workflows and submit approvals |
| Temporal UI | `http://127.0.0.1:8080` | Inspect history, waits, retries, and recovery |
| Grafana | `http://127.0.0.1:3000` | Inspect API, workflow, tool, model, and cost signals |
| Prometheus | `http://127.0.0.1:9090` | Query bounded operational metrics |

Use `make down` to stop the stack without deleting its volumes. The
[local runbook](docs/runbooks/local-development.md) contains the authenticated API sequence and
database lifecycle commands.

For the isolated operator-console demonstration, use `make operator-demo-up` and
`make operator-demo-session`. Its console is at `http://127.0.0.1:18100/console/`;
the [demo guide](docs/operator-demo.md) documents approval and the controlled crash.

## Verify the system

```bash
make lint typecheck test test-security
make test-integration
make demo
```

The integration target creates an isolated Compose project, tests reversible migrations, runs the
reviewed evaluation suite, proves crash recovery, verifies the separately packaged reference
agent, generates evaluation reports and SBOMs, and removes the isolated environment.

The `v0.2.0` release contains evaluation reports and SBOMs for the vendor-onboarding reference
workflow. The [post-release evidence index](docs/evidence/v0.2.0.md) identifies the release commit,
the successful Actions run, the attached artifacts, and the exact limitations.

## Code tour

| Area | Start here |
| --- | --- |
| Durable workflows | [`workflows/vendor_onboarding.py`](src/agents_should_survive_failure/workflows/vendor_onboarding.py), [`workflows/refund/`](src/agents_should_survive_failure/workflows/refund/) |
| Workflow starts | [`workflow_starts.py`](src/agents_should_survive_failure/workflow_starts.py) |
| Governed tools and MCP | [`tool_gateway.py`](src/agents_should_survive_failure/tool_gateway.py), [`mcp_adapter.py`](src/agents_should_survive_failure/mcp_adapter.py) |
| Persistence | [`persistence/models.py`](src/agents_should_survive_failure/persistence/models.py), [`migrations/`](migrations/) |
| Evaluation | [`evaluation_scenarios.py`](src/agents_should_survive_failure/evaluation_scenarios.py), [`evaluation.py`](src/agents_should_survive_failure/evaluation.py) |
| Security | [`docs/threat-model.md`](docs/threat-model.md), [`tests/security/`](tests/security/) |
| External contract | [`packages/agents-should-survive-failure-sdk/`](packages/agents-should-survive-failure-sdk/), [`packages/reference-operations-agent/`](packages/reference-operations-agent/) |
| Observability | [`metrics.py`](src/agents_should_survive_failure/metrics.py), [`deployment/`](deployment/) |

Thirteen [architecture decision records](docs/adr/) explain the main ownership and reliability
trade-offs.

## Scope

This repository is a production-style reference implementation, not a hosted service or a claim
of production readiness. It does not provide multi-tenancy, high availability, enterprise
identity, Kubernetes operation, billing, or hostile-code isolation. External agent packages are
trusted, operator-installed code.

Read the [limitations](docs/limitations.md), [failure cases](docs/failure-cases.md), and
[threat model](docs/threat-model.md) before adapting the design.

## Documentation

- [Technical walkthrough](docs/technical-walkthrough.md)
- [System overview](docs/system-overview.md)
- [Evaluation methodology](docs/evaluation-methodology.md)
- [Tool and agent trust model](docs/security/tool-and-agent-trust.md)
- [Architecture decisions](docs/adr/)

Licensed under the [Apache License 2.0](LICENSE).
