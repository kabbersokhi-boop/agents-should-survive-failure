# 0009 Model Output Budgets

## Status

Accepted.

## Decision

NVIDIA chat-completion requests carry an explicit maximum output-token limit. The evidence layer
also caps stored explanation summaries. Limits are configuration-driven and validated at startup.

## Consequences

Model explanations remain bounded operational artifacts rather than unbounded workflow state.
Deterministic scoring and human authorization remain independent of model output length.
