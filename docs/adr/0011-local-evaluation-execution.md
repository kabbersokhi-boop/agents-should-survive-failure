# ADR 0011: Local evaluation execution boundary

## Status

Accepted; reviewed-catalog semantics clarified by ADR 0013.

## Context

Evaluation runs create durable evidence. Exposing execution through an unnecessary write-capable
HTTP endpoint would expand the operator surface; the authenticated control-plane endpoint remains
the deployment boundary while local maintenance also needs an explicit operator command.

## Decision

Run vendor-onboarding evaluations through `make evaluate` with a required
`EVALUATION_IDEMPOTENCY_KEY`. The target executes inside the running local API container, uses the
same Compose-network database configuration as the control plane, persists the run, and prints its
identifier. Reports remain available through the read-only evaluation API.

This command validates the reviewed catalog and executes the production workflow. Every result
records its workflow execution mode and bounded evidence.

## Consequences

Repeated invocations with the same key reuse the original run. The command does not add a new local
unauthenticated mutation endpoint. A failed catalog record fails the release job; a successful
release run is described with its actual behavioral and recovery evidence.

The isolated Compose release gate invokes the same command after integration tests with a fixed
per-clean-database idempotency key. Credentialed model-provider behavior remains a separate manual
smoke test.
