# ADR 0012: Recoverable workflow starts

## Status

Accepted

## Context

Creating a database run and starting its Temporal workflow are separate durable systems. A process
failure or network timeout between them can leave a committed application intent whose Temporal
execution is unknown. Retrying with a random workflow ID could create duplicate executions.

## Decision

Persist a `workflow_runs` intent and one `workflow_start_attempts` record atomically before any
Temporal call. Idempotency keys are unique only within the requesting principal and are paired with
a canonical request fingerprint; reusing a key for another request returns a conflict. The Temporal
workflow ID is deterministically derived from the persisted run UUID.

The start coordinator leases an attempt before calling Temporal, records safe failure categories,
and accepts Temporal's `WorkflowAlreadyStartedError` as successful reconciliation. A lease token
prevents a late caller from overwriting a later retry's result. The authenticated start route retries
the same intent, and `make recover-workflow-starts` lets an operator reconcile pending or failed
records after a process interruption.

## Consequences

Temporal start is not part of the PostgreSQL transaction, but retries cannot create a second
Temporal workflow execution because every attempt uses the same workflow ID. The release gate
includes injected timeout and already-started scenarios. Recovery still requires Temporal to become
available; the command exits nonzero while unresolved records remain.
