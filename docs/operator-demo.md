# Supplier approval and crash recovery

The demonstration follows one synthetic supplier through authenticated intake,
deterministic policy assessment, an operator decision and a real worker crash.
Temporal owns orchestration. PostgreSQL owns business effects. A successful
database business status does not imply Temporal has acknowledged completion.

## Start a separate local environment

```bash
make setup
make operator-demo-up
make operator-demo-session
```

The `agents-portfolio` Compose project uses separate named volumes and loopback
ports. It does not reset existing environments. Fault injection is enabled only
for this development instance; application settings reject it in production.

| Interface | Address |
| --- | --- |
| Operator console | `http://127.0.0.1:18100/console/` |
| Temporal UI | `http://127.0.0.1:18088` |
| Grafana | `http://127.0.0.1:13000` |
| Prometheus | `http://127.0.0.1:19090` |

The session command creates a scoped synthetic operator key that expires after
four hours and writes it to the Git-ignored `artifacts/operator-session.json` with
owner-only file permissions. Use its `api_key` to connect the console; never
commit or share that file. The browser keeps the key only in page memory, clears
the password field after connection, and clears its state on disconnect.

## Follow one case

1. Enter a fictional supplier and a unique reference. Use an `example.invalid`
   contact address. Start onboarding and select the resulting case.
2. Inspect the deterministic risk score, actual policy citations and bounded
   advisory explanation. The default provider is deterministic, not live NVIDIA
   inference. No model output grants approval authority.
3. Wait for the exact approval request and version. At this point the three
   persisted effect counts are zero, and Temporal remains `RUNNING`.
4. Copy the case UUID into the commands below. Arm the selected case before
   submitting approval. Start the recovery harness in a separate terminal,
   then approve the supplier through the console with your decision rationale.

```bash
make operator-demo-arm RUN_ID=<selected-case-uuid>
make operator-demo-recover RUN_ID=<selected-case-uuid>
```

The harness verifies a consumed post-commit fault and one of each persisted
effect before sending `SIGKILL` to the inspected development worker. It records
exit code 137, restarts **only that existing container**, inspects a different
process ID and waits for Temporal completion. Restart preserves the container's
image, environment, ports and volumes rather than reconciling Compose defaults.
No Docker control is exposed through the browser or application API.

The resulting `artifacts/operator-crash-proof.json` must show the same case ID,
decision activity attempt at least 2, Temporal `COMPLETED`, and counts of exactly
one approval decision, one approved-supplier record and one synthetic notification.
The notification is a local database record, not evidence of delivered email.

## Inspect evidence, not just green dashboards

`GET /api/v1/workflow-runs/{run_id}/business-evidence` requires `runs:read`.
All three counts use one PostgreSQL statement snapshot scoped to the selected
supplier run. Temporal execution status and activity attempts are separately
observed through its client. If that observation fails, the response preserves
the database counts and reports `UNAVAILABLE`—it never invents completion.

The console shows ordered persisted events, policy citations and the actual
approval request version. Its review queue excludes runs linked to evaluation
results; the general workflow-list API remains unchanged unless
`exclude_evaluations=true` is explicitly requested. Grafana metrics are aggregate
operational context, not a substitute for case-specific database verification.

## Verification

```bash
make lint typecheck test test-security
make check-console
```

`scripts/browser_console.cjs` exercises real intake, approval, completion, token
clearing, empty browser storage and mobile layout against a disposable local
instance. It requires Playwright and Chromium; the module and executable can be
provided through `PLAYWRIGHT_MODULE_PATH` and `CHROMIUM_PATH`. Its default session
file is the one created above. It creates a synthetic supplier case, so do not
point it at an operational instance.

To stop this demonstration without deleting its volumes:

```bash
COMPOSE_PROJECT_NAME=agents-portfolio docker compose stop
```

If the harness itself is forcibly terminated, restart only the inspected worker
with `COMPOSE_PROJECT_NAME=agents-portfolio docker compose start worker`, then
inspect Temporal and the case evidence. Do not reset the database to manufacture
a successful outcome.
