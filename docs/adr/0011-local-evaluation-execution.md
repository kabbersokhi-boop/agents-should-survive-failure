# ADR 0011: Local evaluation execution boundary

## Status

Accepted

## Context

Evaluation runs create durable behavioral-contract evidence. The local API has no authentication
layer, so exposing evaluation execution through a write-capable HTTP endpoint would expand its
operator surface without adding an identity or authorization control.

## Decision

Run vendor-onboarding evaluations through `make evaluate` with a required
`EVALUATION_IDEMPOTENCY_KEY`. The target executes inside the running local API container, which
uses the same Compose-network database configuration as the control plane. The command persists
the run and prints its identifier; reports remain available only through the read-only evaluation
API.

## Consequences

Repeated operator invocations with the same key reuse the original run. Local evaluation execution
does not add a new unauthenticated HTTP mutation endpoint. A deployed operator interface must add
authenticated authorization before exposing equivalent remote execution.
