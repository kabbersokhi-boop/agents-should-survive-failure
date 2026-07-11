# ADR 0005: Deterministic policy, tool, and evaluation boundaries

- Status: Accepted
- Date: 2026-07-11

## Decision

Phase 4 uses deterministic local behavior to exercise policy retrieval, tool authorization, and
evaluation persistence without provider calls. Policy retrieval preserves citations, the read-only
vendor lookup gateway fails closed without its required permission and records idempotent invocation
evidence, and the evaluation runner persists one result per enabled case.

## Consequences

The provider phase can replace deterministic embeddings and explanations without changing citation,
tool permission, or evaluation result contracts. CI remains deterministic and credential-free.
