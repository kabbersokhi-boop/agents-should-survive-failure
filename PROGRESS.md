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

## 2026-07-12 Backend Release Gate (Phase 9)

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

The Phase 9 release gate covers the backend repository as it exists at this revision: strict API
contracts, scoped API-key authentication, recoverable Temporal starts, versioned approvals,
agent-owned tool capabilities, migration lifecycle checks, deterministic behavior evaluation, and
secret scanning. It does not evaluate a credentialed NVIDIA model call and does not authorize SDK
work as complete. SDK implementation is intentionally outside the current requested scope.
