# ADR 0013: Versioned Evaluation Contracts

## Status

Accepted for the reviewed evaluation catalog.

## Context

The earlier evaluator recomputed a jurisdiction-derived risk band directly from evaluation input.
It neither started the Temporal workflow nor inspected production evidence, so it could not prove
the project's central failure-survival claim. The release needs a reviewed, stable scenario catalog
before the real executor, controlled fault injection, and active crash tests are built.

## Decision

Store the reviewed suite as package data and validate it with deeply immutable, extra-forbidden
Pydantic contracts. Nested collections are tuples and adversarial tool arguments are exposed as
read-only mappings. The first suite contains exactly one versioned case for each of the 24 required
scenario types. Each case declares:

- synthetic vendor input;
- provider, immutable-grant, and future fault-plan setup;
- approval, cancellation, and adversarial tool-driving instructions;
- exact production workflow event identifiers;
- bounded expectations for every governed tool;
- expected run, vendor, approval, model, projection, and synthetic-email state;
- retry and workflow-start-attempt minimums; and
- duplicate-prevention invariants and required evidence sources.

Persist suite provenance, the complete case contract, review metadata, and a suite-bound case digest.
Suite and case digests use sorted canonical JSON with an explicit timezone-aware timestamp encoding.
PostgreSQL rejects updates to or deletion of reviewed case contract fields. The separate `enabled`
switch remains operator-controlled, and reseeding preserves it while comparing every reviewed field
and failing if content changed without a new suite or case version. Evaluation results snapshot the
case slug, version, digest, and expected outcome so later catalog changes cannot rewrite historical
meaning.

Retain a narrowly scoped catalog-integrity runner so the existing operator and reporting paths
remain usable. It validates all 24 persisted records by reconstructing and hashing their actual
columns. Its records state `workflow_executed=false`; it does not score business behavior.

## Consequences

The release implementation uses real Temporal execution without changing the reviewed dataset shape or losing
source provenance. Database reports can identify the exact suite and case content used for a run.
The catalog is useful design and migration evidence, and the release evaluator produces production-workflow reliability
benchmark and no failure-survival proof.

Changing reviewed case content requires a new suite version and a bumped case version for every
changed case. Adding or changing the future fault-injection mechanism must preserve the declared,
disabled-by-default safety boundary and must not reinterpret historical catalog rows.

The older schema cannot represent the catalog provenance. Downgrading this revision therefore removes
evaluation runs, results, and reviewed case rows after dropping the immutability trigger, while
retaining legacy evaluation data. The isolated migration gate creates catalog evidence, downgrades
one revision, re-upgrades, and reseeds to verify that this explicit data-loss boundary is reversible
at the schema and catalog level.
