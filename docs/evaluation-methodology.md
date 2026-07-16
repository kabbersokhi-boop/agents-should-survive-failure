# Evaluation Methodology

## Phase B1 status

The repository ships the reviewed vendor-onboarding suite at
`src/agents_should_survive_failure/evaluation_datasets/vendor_onboarding.v1.json`.
It contains one case for each of the 24 Phase B scenario types. The JSON is package data and is
validated by deeply immutable, extra-forbidden Pydantic contracts before it can be seeded or used.
Nested tool arguments are read-only after validation, preventing the cached reviewed suite from
being mutated in process.

The suite is pinned by:

- schema version, suite slug, and suite version;
- reviewer label and review timestamp;
- a SHA-256 digest over sorted canonical JSON for the complete normalized suite;
- a suite-bound SHA-256 digest for every case; and
- immutable result snapshots of the case version, case digest, and expected outcome.

The current normalized suite digest is
`f8a11accd7b167224c445213218bba7eb324cc457eb0269c3002cdd89f498904`.
Run `make validate-evaluation-dataset` to parse the packaged JSON, reject unknown fields, require
unique case slugs, require exactly one case for every scenario type, and print the validated digest.

Each case declares synthetic input, setup controls, approval/cancellation or adversarial driving
instructions, exact expected persisted workflow event types, expected tool invocation bounds,
terminal business state, retry/start-attempt minimums, and duplicate-prevention invariants. Event
and tool identifiers are restricted to names that the production workflow currently persists.
Approval sequences are validated across attempts: accepted decisions must precede replay/conflict
checks, same-key replays must repeat the accepted decision, and conflicts must change it.

## B1 catalog integrity runner

`make evaluate` currently runs `b1_catalog_persistence_integrity`. It reads all 24 enabled case rows
for the packaged suite, reconstructs each strict case contract from the stored columns, recalculates
the suite-bound digest, and compares the complete content and review metadata with the packaged
source. It records missing, unexpected, malformed, or drifted cases as failures.

This runner deliberately records `workflow_executed=false` in run configuration, per-case actual
outcome, metrics, evidence, and summaries. It does not derive a risk band, reproduce workflow
business logic, call Temporal, or inspect runtime side effects. A successful B1 evaluation run
means only that the reviewed catalog was persisted without drift.

## Important boundary

Phase B1 is design and provenance evidence, not a reliability benchmark. It does not prove worker
recovery, retry classification, approval safety under delivery races, or exactly-once business
effects. No Phase B pass rate or latency claim should be made from the B1 integrity runner.

Phase B2 must start real workflows through the normal start coordinator or authenticated API, drive
decisions and cancellations, wait on persisted state, and score actual PostgreSQL workflow, event,
approval, tool, model, projection, email, audit, and workflow-start evidence. The B2 scorer must
compare observed evidence with these contracts and must not duplicate production business logic.
Controlled fault plans are declarations only in B1; their safe persisted execution mechanism belongs
to B3, and active worker-crash/exactly-once proof belongs to B4.

## Persistence and downgrade boundary

Reviewed evaluation contract fields are protected by a PostgreSQL update/delete trigger. Operators
may toggle only `enabled`; idempotent seed loading preserves that setting and rejects drift in every
reviewed field. The Phase A schema has no suite provenance or immutable case digest columns, so a
downgrade from B1 explicitly deletes B1 evaluation runs, results, and case rows while retaining
legacy pre-B1 evaluation records. The isolated Compose gate exercises a seeded B1 evaluation,
downgrades one revision, re-upgrades, and reseeds to prevent catalog collisions on re-upgrade.

## Current workflow nuance

The production workflow checks its cancellation signal after the approval-request activity. The
`cancellation_before_approval` case therefore means the cancellation signal is sent before the
request becomes observable, while the expected durable database state still contains one cancelled
approval request. This contract records current behavior explicitly rather than claiming an absent
request that the workflow cannot currently produce.
