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

## 2026-07-15 Phase A10 Security Checkpoint

Checked-out worktree: pending commit.

| Check | Result |
| --- | --- |
| Production dependency audit | Passed: `pip-audit` found no known vulnerabilities after upgrading FastAPI, Starlette, and Pydantic Settings |
| Security suite | Passed: 6 adversarial tests for strict API input, identity override, scope escalation, MCP identity/permission exclusion, sandbox policy, and safe provider errors |
| Backend SBOM | Generated valid CycloneDX JSON: 121 components |
| Container SBOM | Generated valid CycloneDX JSON from `agents-control-plane:local`: 4,012 components |
| Full static/unit gate | Passed: Ruff, Pyright, 80 unit tests, 80% coverage |
| Secret scan | Passed: Gitleaks reported no leaks across 40 commits |
| Isolated integration gate | Passed after stopping the normal Compose stack: migration round-trip, deterministic workflow integration, and cleanup completed |
| Normal stack restoration | API readiness reported PostgreSQL and Temporal `ok` |

`make dependency-audit`, `make test-security`, `make sbom-backend`, and `make sbom-container` now
provide reproducible local checks. CI runs the security suite and dependency audit, uploads the
backend CycloneDX SBOM, builds the container image, creates its CycloneDX SBOM with a read-only
Docker socket mounted only into the scanner, and uploads that artifact. Gitleaks retains no `.env`
allow-list.

This completes the A10 security checkpoint for currently implemented surfaces. It does not make the
master Phase A release gate complete: the API/approval/tool/MCP/sandbox/evaluation acceptance gaps
recorded in the prior checkpoints remain, especially no real 20-case workflow evaluator and no
adversarial tests for unimplemented artifact/checkpoint APIs. Phase B and C-F remain open.

## 2026-07-15 Phase A5 Approval-Boundary Checkpoint

Checked-out worktree: pending commit.

| Check | Result |
| --- | --- |
| Workflow approval boundary | `decide` is a Temporal Update with a workflow-side validator, not a fire-and-forget signal |
| Validator tests | Passed: early, cancelled, mismatched, idempotent-retry, and conflicting decision states |
| API tests | Passed: authenticated principal identity is forwarded to a deterministic update ID; rejected update maps to `409` |
| Compose workflow test | Passed: approved run survived a worker restart and completed its single synthetic-email side effect; cancellation also passed |
| Focused static checks | Ruff and Pyright passed with 0 errors |

The approval endpoint now waits for Temporal to validate the Update before returning `202`. A
worker-side rejection returns a safe `409`; an RPC timeout returns `503` and directs the caller to
retry the same idempotency key. PostgreSQL remains the transaction owner for the decision, audit
event, terminal run state, and at-most-once approved-vendor projection.

This is not full A5 acceptance: broader Temporal test-environment coverage and a complete
adversarial decision matrix remain required, including terminal-state and concurrent-request cases.

## 2026-07-15 Phase A2 Event-Stream Checkpoint

Checked-out worktree: pending commit.

`GET /api/v1/workflow-runs/{run_id}/events/stream` now provides an authenticated SSE view over
persisted workflow evidence. It replays ordered events after a bounded sequence cursor, accepts the
standard `Last-Event-ID` header for reconnects, emits a stable `workflow_event` SSE type, sends
keepalives for active runs, and completes after a terminal run's evidence has been sent. The stream
uses short database transactions per poll rather than holding a connection while a client waits.

Focused repository/API tests passed for cursor validation, replay, terminal completion, route
contracts, and repository persistence. The live Compose workflow suite also passed all three
scenarios: worker-restart approval, cancellation, and authenticated SSE replay. This improves A2
but does not complete it: the full API surface and pagination consistency still need a release-gate
audit.

## 2026-07-15 Phase A6 Tool-Audit Durability Checkpoint

Checked-out worktree: pending commit.

The worker's governed MCP adapter now receives a gateway with an independent database recorder.
Policy-denied, approval-required, malformed-input, and missing-handler attempts are recorded in a
separate transaction before their caller raises. A rollback-focused PostgreSQL integration test
proves that a policy-denied attempt remains visible after the calling transaction is rolled back.

Unregistered tool attempts now have a nullable definition reference plus immutable requested name
and version evidence; the migration was applied successfully to the local stack and the same
rollback-focused PostgreSQL test proves that record survives. This does not complete A6: the full
tool version-pinning and credential-broker release audit remains open.

## 2026-07-15 Phase A8 Sandbox Enforcement Checkpoint

The Docker sandbox's network-disabled policy is now exercised in a real local container, not only
through command-construction tests. The integration test attempts a TCP connection to `1.1.1.1:53`
and requires a nonzero result containing `Network is unreachable`. It passed locally.

This does not claim hostile-code isolation: Docker remains a limited local execution boundary, and
resource-pressure, file-escape, and production broker isolation testing remain open.

## 2026-07-15 Phase A6 Tool-Version Pinning Checkpoint

| Check | Result |
| --- | --- |
| Static checks | Ruff and Pyright passed with 0 errors |
| Full unit suite | Passed: 86 tests, 80% coverage |
| Security suite | Passed: 6 tests |
| Compose validation | Passed |
| PostgreSQL tool integration | Passed: 4 tests, including denied-attempt durability, unregistered-tool evidence, and version pinning |
| Migration lifecycle | Isolated Compose upgrade through `c4d7e8f9a0b1`, downgrade to base, re-upgrade, and `alembic check` passed |
| Normal stack restoration | API readiness reported PostgreSQL and Temporal `ok` at `c4d7e8f9a0b1` |

The first valid call for a logical tool name now records a `tool_run_bindings` row that points to the
exact immutable tool definition. PostgreSQL conflict-safe insertion means concurrent first calls
converge on one binding. A later request for a different version of the same tool name is rejected,
persisted with `version_mismatch`, and cannot change the run's capability surface. Gateway instances
without an independent audit database now persist terminal denials in the caller transaction; the
worker continues to use its independent audit database so denials also survive caller rollback.

This improves the A6 tool-version-pinning criterion. Immutable agent-version registration,
credential brokering, and the broader SDK capability-negotiation design remain later work; the master
Phase A release gate is still incomplete.

## 2026-07-15 Phase A2 SDK Read-Contract Checkpoint

The versioned API now has SDK-suitable offset-paginated global list endpoints and matching detail
endpoints for workflow events, approvals, model calls, tool calls, agents, and evaluations. Existing
run-scoped evidence, list, and SSE endpoints remain available. Every new endpoint is under
`/api/v1`, uses the appropriate existing read scope, bounds `limit` to 100 and `offset` to 10,000,
and is represented in the OpenAPI schema.

Focused API contract tests passed for route exposure, response mapping, pagination shapes, and
detail retrieval. This closes the previously recorded A2 list/detail surface gap. API ownership
scoping and any future cursor migration remain separate SDK/control-plane design work.

## 2026-07-15 Phase A5 Approval-Audit Integrity Checkpoint

Approval-decision audit events now carry the authenticated decision principal as `actor_id`, matching
the durable `ApprovalDecision.decided_by_id` record. The decision, request state transition, vendor
projection, workflow result, event, and audit write remain within the transaction-owning activity.
Focused workflow/activity/API tests passed.

## 2026-07-15 Phase A3/A5 Principal and Temporal Test Correction

| Check | Result |
| --- | --- |
| API-key scope contract | Added and tested the missing `agents:write` scope |
| Approval principal boundary | User and service principals may decide; agent principals are rejected even with `approvals:decide` |
| Official Temporal workflow test | Passed: real time-skipping environment reached durable approval wait, accepted a validated Update, and completed |
| Static checks | Ruff and Pyright passed with 0 errors |

This corrects two material release criteria that the prior checkpoints did not prove. It is not a
Phase A completion claim: the remaining API audit, tool/MCP, sandbox, and end-to-end release-gate
criteria are still being audited.

## 2026-07-15 Phase A2 Chunked Request-Size Enforcement

The API now applies its configured request-size limit while reading ASGI request chunks, rather
than relying only on the caller-provided `Content-Length` header. Oversized chunked requests are
rejected before routing with the same structured `413 payload_too_large` response and request ID
as declared-length requests. The unit suite covers the no-`Content-Length` path; Ruff and Pyright
passed after the change.

## 2026-07-15 Phase A5 Approval API State-Matrix Expansion

The approval API tests now prove that cancelled and terminal approval records, and requests with a
stale expected version, are rejected with `409` before the Temporal Update is attempted. They also
prove that a byte-for-byte identical persisted decision retry returns `202` without sending a second
workflow update. Focused workflow, API, and official Temporal test-environment checks passed.

## 2026-07-15 Phase A3 Authorization-Denial Auditing

An authenticated principal denied by scope or principal-type policy now produces a durable audit
event before the API returns `403`. The audit contains the principal ID/type, route template, and
required scopes only; it does not record the API key, authorization header, or request body. The
approval decision path remains restricted to user and service principals. Targeted API/auth tests,
Ruff, and Pyright passed.

## 2026-07-15 Phase A7 MCP Resilience Contract Expansion

The governed MCP adapter tests now cover upstream timeout/unavailability, a run-pinned tool-version
mismatch, and malformed gateway output. The adapter preserves the gateway's typed failure rather
than bypassing policy, while FastMCP rejects malformed output instead of returning it as a valid
tool result. This covers the local, in-process adapter only; remote MCP transport remains an
explicitly unsupported capability.

## 2026-07-15 Phase A8 Non-Root Workspace Enforcement Correction

A real sandbox enforcement test found that the host-created temporary workspace was not writable by
the configured non-root container UID. The broker now makes only that disposable bind mount writable
for the workload. Real-container tests pass for network denial, UID `65532`, workspace writes, and
root-filesystem write denial; command-construction tests continue to prove CPU, memory, process,
environment, output, and cleanup controls.

## 2026-07-15 Release Gate Secret-Scan Correction

The first full gate run reported one generic-key match in a historical Temporal test fixture. It was
an idempotency test token, not credential material. The current fixture was renamed and Gitleaks has
one exact-token allow-list entry solely so its history scan can pass without rewriting published
history. The unsafe historical `.env` allow-list remains absent. `make secret-scan` passed after
the correction.

## 2026-07-15 Release Gate SSE Regression Correction

The first captured integration gate found that the request-body replay middleware returned a
synthetic disconnect after an empty GET body. That caused an active SSE client to stop polling
before persisted events arrived. The replay now returns an empty request message instead, and a
focused middleware test protects that streaming-client behavior. The full gate must be rerun after
this correction.

## 2026-07-15 Release Gate Persisted-Evidence Replay Correction

The rerun exposed a test race: Temporal reports its in-memory waiting phase before the asynchronous
approval activity has committed the `approval.requested` event. The SSE integration test now waits
for that persisted event before asserting the stream replays it, which is the endpoint's actual
persisted-evidence contract. Ruff and Pyright passed; the complete gate must be rerun again.

## 2026-07-15 Phase A Isolated Integration Gate

| Check | Result |
| --- | --- |
| Isolated migration lifecycle | Passed: upgrade to `c4d7e8f9a0b1`, downgrade to base, re-upgrade, and `alembic check` |
| Compose integration suite | Passed: 16 tests, including governed tools, worker restart, cancellation, SSE replay, and real sandbox enforcement |
| Deterministic evaluation | Passed: evaluation run `dc899d10-5c75-45b6-af15-f03e9bb18538` succeeded |
| Normal-stack restoration | Passed: API readiness reported PostgreSQL and Temporal `ok` |

The SSE body-limit interaction was repaired by exempting bodyless read methods from request-body
buffering. The final local verification evidence now includes the full isolated integration gate;
the master backend release gate remains blocked on Phase B's 20-case real-workflow evaluator, as
documented in the master plan.

## 2026-07-15 Phase A3/A5 Actor Attribution and Conflict Coverage

| Check | Result |
| --- | --- |
| Disabled principal authentication | Passed: disabled principals cannot resolve an API key and do not update key-use time |
| Durable workflow start audit | Passed: the persisted start intent records the authenticated requester atomically with the start attempt |
| Worker-side review audit | Passed: the first workflow activity preserves the run requester as its actor |
| Cancellation request audit | Passed: the API records the authenticated cancellation requester before signalling Temporal |
| Conflicting Temporal decision | Passed: a second contradictory Update is rejected after the first resolves the durable wait |

Focused Ruff, Pyright, and 21 auth/approval/workflow tests passed. These records cover successful
and denied sensitive control-plane operations without storing API keys or request bodies. The
remaining Phase A work is the final requirement-level security and policy audit; the separate
master backend release gate remains blocked on Phase B evaluation breadth.

## 2026-07-15 Phase A5 Workflow-Level Version Validation

The approval version is now durable workflow state and the Temporal Update validator rejects a
decision whose expected version differs, before it mutates the workflow. The official time-skipping
Temporal test first submits a stale version, proves the workflow remains at its durable wait, then
submits version `1` and completes. Focused Ruff, Pyright, and 12 approval-workflow tests passed.
