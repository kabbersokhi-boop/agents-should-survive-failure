# Demonstration Guide

This guide is designed for a five-to-ten-minute employer demonstration. Use synthetic vendor data only.

## Recruiter-friendly path

1. Explain the business problem: supplier onboarding can touch payments and sensitive systems, so retries and uncertain timeouts must not create duplicate effects.
2. Start the local stack with `make up` and show `curl http://127.0.0.1:8000/health/ready`.
3. Create a vendor and start onboarding using the authenticated API flow in the [local runbook](runbooks/local-development.md).
4. Show that the workflow waits durably for human approval rather than asking the model to approve itself.
5. Approve the case and show the completed result and persisted evidence.

## Technical hiring-manager path

1. Open Temporal UI at `http://127.0.0.1:8080` and show the workflow history and approval wait.
2. Query the evidence API or PostgreSQL to show the durable audit trail and business projection.
3. Run `make test-worker-crash` and call out the `SIGKILL`, replacement worker, and final one-row counts.
4. Open the [24-case evaluation report](../artifacts/evaluations/evaluation-1b3f5e31-b437-4ff2-bb2f-d59350d43d1a.md) or present the committed [release evidence](evidence/v0.2.0.md).
5. Run `make test-managed-agent` to demonstrate the external Operations Investigation Agent.
6. Point to a checkpoint, digest-verified artifact, budget record, and governed tool invocation in the managed-agent evidence.

## Talking points

**Why Temporal and PostgreSQL?** Temporal owns durable orchestration and redelivery. PostgreSQL owns domain state, evidence, uniqueness constraints, and business-effect idempotency. Each system has one clear job.

**Why at-least-once execution?** Distributed workers can lose an acknowledgement after an effect commits. Redelivery is the honest execution model.

**How do effects happen once?** Stable idempotency keys, transactions, and database constraints make repeated activity delivery converge on one business effect.

**Why can’t the model approve?** The model is advisory. Deterministic policy and a human approval update authorize work, and governed tools enforce the resulting grant.

**What does the SDK prove?** The preview SDK gives a trusted, operator-installed external agent a typed contract for checkpoints, artifacts, budgets, events, and governed tools.

**What remains preview?** The SDK/runtime and delegation surfaces are preview or experimental; external packages are trusted code and Docker is not hostile-code isolation.
