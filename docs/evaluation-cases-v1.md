# Phase B Evaluation Cases v1

Canonical source: `src/agents_should_survive_failure/evaluation_datasets/vendor_onboarding.v1.json`.
This table is a review aid; the JSON and its strict schema are the executable contract.

- Suite: `vendor-onboarding-phase-b` version `1.0.0` (schema `1`)
- Cases: 24
- Normalized suite SHA-256: `f8a11accd7b167224c445213218bba7eb324cc457eb0269c3002cdd89f498904`

| # | Case slug | Scenario | Run / approval | Retry / starts | Effects | Driver or fault control |
| ---: | --- | --- | --- | --- | --- | --- |
| 1 | `low-risk-approved` | `low_risk_approved` | `succeeded / approved` | `activity>=0, starts>=1` | projection=1, email=1 | normal production path |
| 2 | `low-risk-rejected` | `low_risk_rejected` | `rejected / rejected` | `activity>=0, starts>=1` | projection=0, email=0 | normal production path |
| 3 | `high-risk-approved` | `high_risk_approved` | `succeeded / approved` | `activity>=0, starts>=1` | projection=1, email=1 | normal production path |
| 4 | `high-risk-rejected` | `high_risk_rejected` | `rejected / rejected` | `activity>=0, starts>=1` | projection=0, email=0 | normal production path |
| 5 | `cancel-before-approval` | `cancellation_before_approval` | `cancelled / cancelled` | `activity>=0, starts>=1` | projection=0, email=0 | cancel: before_approval |
| 6 | `cancel-waiting-for-approval` | `cancellation_waiting_for_approval` | `cancelled / cancelled` | `activity>=0, starts>=1` | projection=0, email=0 | cancel: waiting_for_approval |
| 7 | `early-approval-rejected` | `early_approval_rejected` | `succeeded / approved` | `activity>=0, starts>=1` | projection=1, email=1 | approval: rejected_early |
| 8 | `stale-approval-version-rejected` | `stale_approval_rejected` | `succeeded / approved` | `activity>=0, starts>=1` | projection=1, email=1 | approval: rejected_stale |
| 9 | `conflicting-approval-rejected` | `conflicting_approval_rejected` | `succeeded / approved` | `activity>=0, starts>=1` | projection=1, email=1 | approval: rejected_conflict |
| 10 | `idempotent-approval-replay` | `idempotent_decision_replay` | `succeeded / approved` | `activity>=0, starts>=1` | projection=1, email=1 | approval: idempotent |
| 11 | `approval-idempotency-conflict` | `decision_idempotency_conflict` | `succeeded / approved` | `activity>=0, starts>=1` | projection=1, email=1 | approval: rejected_idempotency_conflict |
| 12 | `model-provider-failure-safe` | `model_provider_failure` | `succeeded / approved` | `activity>=0, starts>=1` | projection=1, email=1 | model: fail_explanation |
| 13 | `policy-retrieval-failure-safe` | `policy_retrieval_failure` | `failed / absent` | `activity>=0, starts>=1` | projection=0, email=0 | fault: tool.internal_policy_search.before_execute |
| 14 | `required-tool-permission-denied` | `tool_permission_denied` | `failed / absent` | `activity>=0, starts>=1` | projection=0, email=0 | omit grant: vendor_database_query |
| 15 | `required-tool-timeout-retry` | `tool_timeout_retry` | `succeeded / approved` | `activity>=1, starts>=1` | projection=1, email=1 | fault: tool.vendor_database_query.before_execute |
| 16 | `worker-restart-active-activity` | `worker_restart_active_activity` | `succeeded / approved` | `activity>=1, starts>=1` | projection=1, email=1 | fault: worker.active_activity |
| 17 | `worker-restart-waiting-approval` | `worker_restart_waiting_approval` | `succeeded / approved` | `activity>=0, starts>=1` | projection=1, email=1 | fault: worker.waiting_for_approval |
| 18 | `worker-crash-after-effect-commit` | `worker_crash_after_effect_commit` | `succeeded / approved` | `activity>=1, starts>=1` | projection=1, email=1 | fault: email.send.after_commit_before_ack |
| 19 | `duplicate-projection-prevented` | `duplicate_projection_prevented` | `succeeded / approved` | `activity>=1, starts>=1` | projection=1, email=1 | fault: projection.after_commit_before_ack |
| 20 | `duplicate-email-prevented` | `duplicate_email_prevented` | `succeeded / approved` | `activity>=1, starts>=1` | projection=1, email=1 | fault: email.send.after_commit_before_ack |
| 21 | `ambiguous-workflow-start-recovery` | `ambiguous_workflow_start_recovery` | `succeeded / approved` | `activity>=0, starts>=2` | projection=1, email=1 | fault: workflow_start.after_temporal_accept |
| 22 | `database-transient-failure` | `database_transient_failure` | `succeeded / approved` | `activity>=1, starts>=1` | projection=1, email=1 | fault: database.activity_transaction.before_commit |
| 23 | `malformed-tool-input-rejected` | `malformed_tool_input_rejected` | `succeeded / approved` | `activity>=0, starts>=1` | projection=1, email=1 | tool probe: rejected_invalid_input |
| 24 | `unauthorized-sensitive-operation` | `unauthorized_sensitive_operation` | `succeeded / approved` | `activity>=0, starts>=1` | projection=1, email=1 | approval: forbidden |

Every case requires all nine persisted evidence sources. Expected workflow events use only the
seven event names emitted by the production activities, and every case declares bounded counts
for all three governed tool definitions. Duplicate-prevention expectations cap approval decisions,
approved-vendor rows, and synthetic-email rows at one and require unique event sequences.

The B1 runner validates catalog persistence for all 24 rows only. Real Temporal execution begins
in B2; fault plans are implemented in B3; active crash and exactly-once proof are implemented in B4.
