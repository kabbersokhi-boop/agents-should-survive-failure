# Demonstration Guide

This guide presents the repository as a reliability and governance platform for AI-assisted business workflows. Vendor onboarding is the reference scenario used to prove the system; it is not the general product category.

Use synthetic data only.

## What the audience should understand

By the end of the demonstration, the audience should understand five things:

1. The AI helps interpret evidence but cannot authorize its own recommendation.
2. The workflow can wait durably for a person.
3. Every consequential action passes through controlled code and tools.
4. A worker crash does not erase the case.
5. Redelivery does not create duplicate business effects.

## What appears on screen

A local demonstration uses one terminal and a few browser tabs:

| Interface | Local address | What to show |
| --- | --- | --- |
| FastAPI/OpenAPI | `http://127.0.0.1:8000/docs` | Create the vendor, start onboarding, inspect approvals, and submit a decision |
| Temporal UI | `http://127.0.0.1:8080` | Workflow history, current phase, approval wait, retries, and completion |
| Grafana | `http://127.0.0.1:3000` | API, worker, workflow, model, tool, and approval health |
| Terminal | local shell | Start the stack and run the worker-crash proof |
| Evidence API or PostgreSQL | API/SQL | Ordered events, model metadata, tool calls, approval, projection, and synthetic email |

There is no custom customer-facing dashboard in this reference repository. A real product would normally put a simpler business UI in front of these services.

## Example story

Use a fictional company, **Northstar Retail**, onboarding **Orion Payments**.

Orion will process refunds and may access financial operations data. Northstar wants AI assistance collecting and explaining evidence, but it does not want the model to approve a sensitive supplier or accidentally repeat an action after a crash.

A valid synthetic vendor request uses the API fields:

```json
{
  "external_reference": "orion-payments-demo",
  "legal_name": "Orion Payments",
  "jurisdiction": "IN",
  "contact_email": "orion@example.invalid"
}
```

## Seven-minute employer demonstration

### 1. Open with the problem — 45 seconds

Show the first section of the README or a single architecture image.

Say:

> Most AI demos show what happens when everything works. This project focuses on what happens when a worker crashes, a timeout makes the outcome uncertain, an approval arrives twice, or an agent asks to perform an action it should not authorize.

Do not begin with Temporal, PostgreSQL, MCP, or the folder structure.

### 2. Start the platform — 30 seconds

```bash
make up
curl http://127.0.0.1:8000/health/ready
```

Say:

> This starts the API, durable workflow engine, PostgreSQL record store, worker, and local observability stack.

### 3. Create and start the case — 60 seconds

Open FastAPI/OpenAPI at `http://127.0.0.1:8000/docs`.

Use the authenticated API sequence in the [local-development runbook](runbooks/local-development.md) to:

1. create the synthetic vendor;
2. start its onboarding workflow with a unique idempotency key.

Say:

> The API first persists a recoverable workflow-start intent. It does not simply send a start request and hope the acknowledgement arrives.

### 4. Show the durable approval wait — 60 seconds

Open Temporal UI and select the new workflow.

Show that review and risk assessment completed and that the workflow is waiting for approval.

Say:

> Temporal is acting like a durable case manager. The workflow can remain here without keeping its progress only in process memory.

### 5. Explain the authority boundary — 45 seconds

Show the persisted risk and policy evidence through the evidence API or database.

Say:

> The score is deterministic. The model may explain the evidence, but it is explicitly instructed not to recommend or authorize the final decision. An authenticated principal must approve or reject the case.

### 6. Approve and complete the case — 60 seconds

Submit the approval through the authenticated API. Return to Temporal UI and show the workflow completing.

Then show the persisted outcome:

- one approval decision;
- one approved-vendor projection;
- one synthetic email;
- ordered workflow and audit events.

Say:

> The approval is versioned and idempotent. The final effects are committed through controlled transaction-owning activities and governed tools.

### 7. Run the failure proof — 2 minutes

```bash
make test-worker-crash
```

Explain the proof while it runs:

1. a real workflow is approved;
2. the approval, projection, and synthetic email commit;
3. the worker is deliberately killed with `SIGKILL` before its completion acknowledgement;
4. a replacement worker starts;
5. Temporal redelivers the activity;
6. idempotency and database constraints prevent duplicate effects;
7. the workflow completes.

The final output should report one decision, one projection, and one email.

Say:

> This is not magical exactly-once execution. The activity can be delivered again. The business effect is committed once.

## Optional technical extensions

### Show observability

Open Grafana and briefly show the system-health dashboard. Explain that metrics use bounded labels rather than raw prompts, credentials, or arbitrary identifiers.

### Show the external-agent SDK preview

```bash
make test-managed-agent
```

Explain:

> The separate Operations Investigation Agent is installed as its own Python package. The platform gives it a constrained context for governed tools, checkpoints, artifacts, budgets, events, and approval. This proves the extension model, but the SDK/runtime remains preview quality.

Do not lead with this proof. The vendor workflow is the stronger and more mature demonstration.

### Show the 24-case evaluation

Present the committed [`v0.2.0` evidence summary](evidence/v0.2.0.md) and the evaluation assets attached to the [GitHub release](https://github.com/kabbersokhi-boop/agents-should-survive-failure/releases/tag/v0.2.0).

Explain that the evaluator runs real Temporal workflows and scores persisted PostgreSQL evidence across approval, rejection, cancellation, stale and conflicting decisions, model and tool failures, authorization denial, ambiguous starts, duplicate-effect prevention, and worker interruption.

## Recruiter-friendly 30-second script

> I built a reliability and governance layer for AI-assisted business workflows. The reference example onboards a sensitive supplier: AI gathers and explains evidence, but a human must approve the final action. The workflow survives crashes and retries, records an audit trail, and prevents duplicate business effects. I proved it with 24 real workflow scenarios and a test that deliberately kills the worker after the database commit and recovers with exactly one approval, one approved record, and one notification.

## Hiring-manager two-minute script

> The project separates orchestration, business truth, model advice, human authority, and external effects. Temporal owns durable workflow history and redelivery. PostgreSQL owns business state, audit evidence, uniqueness, and idempotency. The model produces a bounded explanation of deterministic risk and cannot approve the case. Tool calls are checked against a run-specific grant snapshot, exact version, input schema, approval requirements, and idempotency key. The strongest proof kills the real Docker worker after consequential effects commit but before Temporal receives the acknowledgement. A replacement worker receives the activity again, the workflow completes, and database constraints plus stable idempotency keys preserve exactly one business outcome.

## Talking points

**Why is vendor onboarding not the whole product?** It is the complete reference scenario used to prove general reliability and governance patterns for consequential AI-assisted workflows.

**Why Temporal and PostgreSQL?** Temporal owns durable orchestration and redelivery. PostgreSQL owns domain state, evidence, uniqueness constraints, and business-effect idempotency.

**Why at-least-once execution?** A distributed worker can lose an acknowledgement after an effect commits. Redelivery is the honest execution model.

**How do effects happen once?** Stable idempotency keys, transactions, and database constraints make repeated activity delivery converge on one business effect.

**Why can’t the model approve?** Explanation and authorization are different responsibilities. Deterministic policy and an authenticated human or service authorize consequential work.

**What does the SDK prove?** A trusted, operator-installed external Python agent can use a typed, constrained contract for tools, checkpoints, artifacts, budgets, events, and approval.

**What remains preview?** The generic SDK/runtime and delegation surfaces are preview or experimental. External packages are trusted code, and Docker is not hostile-code isolation.

## What not to do

Do not open the demonstration with:

- the folder tree;
- dependency files;
- migration details;
- all Docker services;
- delegation code;
- claims that the platform is production-ready;
- claims that any existing agent can be connected without adaptation.

Lead with the costly failure, show the approval boundary, kill the worker, and prove the single business outcome.
