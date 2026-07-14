# Backend Readiness Progress

## 2026-07-12 Baseline

Checked-out revision: `5c7d062 feat: fail release evaluations on contract violations`.

| Check | Result |
| --- | --- |
| `uv sync --frozen --all-groups` | Passed |
| `make lint` | Passed |
| `make typecheck` | Passed: 0 Pyright errors |
| `make test` | Passed: 41 unit tests, 82% coverage |
| `make compose-check` | Passed |
| `make secret-scan` | Passed: no leaks reported |
| `make test-integration` | Passed after stopping the normal Compose stack to free port 5432 |
| Integration test inventory | 10 collected tests |
| Migration head | `4d902f85f639` |
| Local readiness | PostgreSQL and Temporal reported `ok` |

The documented vendor flow was exercised against the local API: vendor creation, onboarding start,
durable wait for approval, evidence retrieval, approval submission, and terminal completion all
succeeded. The run ID was `0056740d-84a2-4c11-a946-7f4c4608e78c`.

The audit is recorded in
[docs/reviews/backend-readiness-2026-07.md](docs/reviews/backend-readiness-2026-07.md). The
backend has not passed the hardened release gate; Phase A work is required before any SDK work.

## 2026-07-12 Workflow-Start Recovery Checkpoint

Checked-out revision: `af5debe feat: recover persisted Temporal workflow starts`.

| Check | Result |
| --- | --- |
| `make lint` | Passed |
| `make typecheck` | Passed: 0 Pyright errors |
| `make test` | Passed: 68 unit tests, 81% coverage |
| `make compose-check` | Passed |
| `make secret-scan` | Passed: no leaks reported |
| Isolated migration round trip | Passed through migration head `a81879aa36e9` |
| Workflow-start PostgreSQL integration test | Passed |
| Local readiness | PostgreSQL and Temporal reported `ok` |

The control plane now persists workflow intent before a Temporal handoff, uses a stable Temporal
workflow ID, scopes idempotency keys to the authenticated principal, rejects a replay whose request
fingerprint differs, and records retry ownership with a short lease. Timeout and
`WorkflowAlreadyStartedError` reconciliation are covered by unit and PostgreSQL integration tests.
`make recover-workflow-starts` is available to reconcile persisted pending or failed handoffs.

This is a hardening checkpoint, not the backend release gate. Approval concurrency, tool isolation,
evaluation breadth, API completion, and SDK work remain open.

## 2026-07-12 Local Backend Checkpoint (Not the Master Release Gate)

Checked-out revision: `cfe207c ci: require evaluation in isolated release gate`.

| Gate evidence | Result |
| --- | --- |
| `make verify` | Passed |
| Unit suite | Passed: 71 tests, 81% coverage |
| Static checks | Ruff formatting/lint and Pyright passed |
| Configuration and secrets | Compose validation and Gitleaks scan passed |
| Database lifecycle | Isolated PostgreSQL migration upgrade, downgrade to base, and re-upgrade passed |
| Integration suite | Passed against the isolated Compose API, worker, PostgreSQL, and Temporal stack |
| Evaluator | Passed and persisted a `release-gate-v1` evaluation run inside the isolated stack |
| Local restoration | Normal API readiness reported PostgreSQL and Temporal `ok` after the gate |

This was a local repository checkpoint, not the master prompt's backend release gate. It does not
prove the required governed MCP implementation, sandbox, end-to-end telemetry, twenty real
workflow evaluations, worker failure during an activity, vulnerability scan/SBOM, or the SDK
phases. It must not be used to claim that Phase A, Phase B, or the backend release gate is complete.

## 2026-07-12 Governed MCP Checkpoint

Checked-out worktree: pending commit.

| Check | Result |
| --- | --- |
| Official SDK verification | Verified the official MCP Python SDK stable v1 branch; pinned `mcp==1.28.1` |
| Unit suite | Passed: 74 unit tests, 80% coverage |
| Static checks | Ruff formatting/lint and Pyright passed |
| PostgreSQL tool integration | Passed: typed policy search, approval-gated synthetic email, retry idempotency, and denied-attempt persistence |
| Reference workflow integration | Passed: real API/Temporal worker path invoked vendor lookup, policy search, and a single post-approval synthetic email |
| Isolated migration lifecycle | Upgrade to `e4f6a2b1c9d8`, downgrade to base, and re-upgrade completed in the Compose gate |

The platform now has a run-scoped local MCP adapter over the governed gateway. It binds run, agent,
and correlation context in the managed host; it does not expose identity or permissions in MCP tool
arguments. `email.send` persists only a synthetic message and requires a durable approved decision.
Remote MCP transport/authentication and outage handling, sandboxing, full telemetry, real workflow
evaluation, and all SDK phases remain incomplete.

## 2026-07-13 Local Sandbox Checkpoint

Checked-out worktree: pending commit.

| Check | Result |
| --- | --- |
| Unit suite | Passed: 80 unit tests, 80% coverage |
| Sandbox policy tests | Passed: restrictive Docker flags, denied environment names, timeout, output limit, cleanup |
| Local Docker demonstration | Passed: `make sandbox-demo` printed `sandbox demonstration completed` from a bounded container |

The sandbox is a local host-operated broker, not an HTTP endpoint. Workloads run non-root with a
read-only root filesystem, a disposable workspace, network disabled, capability drop,
`no-new-privileges`, CPU/memory/process limits, a timeout, and bounded output. The workload has no
Docker socket. Docker is not a complete hostile-code boundary, and production isolation remains a
documented future hardening requirement.

## 2026-07-15 Phase A9 Observability Checkpoint

Checked-out worktree: pending commit.

| Check | Result |
| --- | --- |
| Static checks | Ruff formatting/lint and Pyright passed: 0 errors |
| Unit suite | Passed: 80 tests, 80% coverage |
| Compose validation | Passed |
| Deterministic integration workflow | Passed: 2 API/Temporal tests, including a worker restart |
| Prometheus | Verified `agents-worker` target is `up=1`; real model, governed-tool, approval, run, sandbox, and worker metric families are exposed |
| Tempo | Verified worker-side workflow, activity, SQL, model, and governed-tool spans from a real workflow |

The worker now exports its own Prometheus endpoint. API and worker install OpenTelemetry providers;
the official Temporal Python `TracingInterceptor` links Temporal start, workflow, signal, and activity
spans with API/model/tool work. Prometheus has route-stable API labels and low-cardinality lifecycle,
model, tool, approval, sandbox, and worker metrics. The local Grafana dashboard now covers workflow
outcomes, tool operations and denials, model latency, approvals, sandbox outcomes, and active states.

Trace verification inspected one deterministic workflow containing `StartWorkflow`,
`RunWorkflow:VendorOnboardingWorkflow`, `RunActivity` spans for review/risk/approval/cancellation,
`agents.model.call`, `agents.tool.invoke`, and `agents.workflow.start`. No prompts, credentials, or
private reasoning were added as telemetry attributes.

This completes the current A9 observability checkpoint, not all of Phase A. A10 remains: dependency
vulnerability scanning, SBOM generation, and the full security test suite. Phase B's real 20-case
workflow evaluator and every SDK/release phase (C-F) also remain open.
