# 0007 Retrieval-Grounded Risk Explanations

## Status

Accepted.

## Context

Risk scoring is deterministic, while model output is explanatory. To make explanations inspectable,
the workflow needs an explicit record of the policy material supplied to the model.

## Decision

The risk-assessment activity retrieves policy citations before requesting an explanation. It stores
the citation identifiers, titles, and source URIs as an append-only workflow event and records the
model-call summary separately. The prompt explicitly prohibits approval recommendations.

Provider failures are recorded as failed model-call evidence but do not alter the deterministic risk
score or prevent the workflow from proceeding to human approval.

## Consequences

Each explanation has durable policy provenance without storing private reasoning. Human approval
remains the sole authorization boundary for vendor onboarding.
