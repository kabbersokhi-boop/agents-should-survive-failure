# ADR 0004: Durable vendor-onboarding workflow

- Status: Accepted
- Date: 2026-07-11

## Context

Vendor onboarding spans deterministic assessment, a human approval pause, and final application
writes. A process restart while approval is pending must not lose the pending decision or allow
the model layer to make the final authorization decision.

## Decision

Temporal owns the `vendor_onboarding` workflow on the `vendor-onboarding` task queue. The workflow
uses named, retrying activities with a three-attempt retry policy. Activities own PostgreSQL
transactions and apply idempotent transitions: start review, calculate a deterministic jurisdiction
risk score, create one approval request, record an approval or rejection, or cancel the run.

The workflow exposes a query for its phase and approval-request identifier. It accepts durable
`decide` and `cancel` signals. A decision signal includes an approver identity and idempotency key;
the database stores the decision, event, audit entry, and approved-vendor projection atomically.
The API creates submitted vendors, starts runs by idempotency key, sends approval or cancellation
signals, and queries workflow status. It does not implement workflow state transitions itself.

## Consequences

Temporal can recover a paused workflow on another worker without replaying database effects as new
events. PostgreSQL remains the queryable business and audit record, while Temporal remains the
durable execution record. The current assessment is deliberately deterministic; a provider-backed
explanation is deferred to the provider phase and cannot authorize a final approval.
