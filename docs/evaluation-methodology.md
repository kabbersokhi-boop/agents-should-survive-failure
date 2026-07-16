# Evaluation Methodology

## Production evaluation status

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

## Production runner

`make evaluate` runs all 24 reviewed cases through the real Temporal vendor-onboarding workflow.
It creates isolated vendors and persisted start intents, drives reviewed decisions and cancellation
probes, applies persisted controlled faults, then scores PostgreSQL workflow, event, approval, tool,
model, projection, email, audit, and workflow-start evidence. JSON and Markdown exports contain
only bounded evidence; they exclude prompts, credentials, private arguments, and reasoning.

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
