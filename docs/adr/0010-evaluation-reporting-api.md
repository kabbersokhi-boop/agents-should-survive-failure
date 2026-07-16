# 0010 Evaluation Reporting API

## Status

Accepted; extended by ADR 0013.

## Decision

`GET /evaluation-runs/{evaluation_run_id}` provides a read-only report of persisted evaluation
configuration and per-case outcome summaries. Phase B1 extends the response with suite slug,
suite/schema versions, dataset digest, immutable case version and digest snapshots, expected and
actual outcome summaries, failure category, duration, workflow-run link, and bounded evidence.
It returns no evaluation input payloads, prompts, credentials, or private model reasoning.

## Consequences

Reviewers can identify the exact reviewed contract behind a result while PostgreSQL remains the
source of record. B1 reports explicitly disclose that no workflow executed; later phases can attach
the real workflow-run ID and runtime evidence without changing historical B1 meaning.
