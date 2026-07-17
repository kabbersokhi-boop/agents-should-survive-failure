# ADR 0005: Deterministic policy, tool, and evaluation boundaries

- Status: Accepted; evaluation implementation superseded in part by ADR 0013
- Date: 2026-07-11

## Decision

The reference workflow uses deterministic local behavior to exercise policy retrieval, tool authorization, and
evaluation persistence without external provider calls. Policy retrieval preserves citations, the
read-only vendor lookup gateway fails closed without its required permission and records idempotent
invocation evidence, and evaluation runs persist one result per selected case.

ADR 0013 replaces the original one-case, jurisdiction-derived evaluator with a reviewed versioned
catalog and a persistence-integrity runner. Real workflow behavior scoring is covered by the release evaluator.

## Consequences

Provider adapters can change without changing citation, tool permission, or evaluation result
contracts. CI remains deterministic and credential-free. A successful catalog evaluation covers
the reviewed contract and the release evaluator covers workflow reliability evidence.
