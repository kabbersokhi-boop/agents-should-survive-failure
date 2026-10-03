# A supplier decision that survives failure

An operations team has approved a supplier. Its worker saves the business outcome,
then dies before the workflow engine receives acknowledgement. The team needs
both continuity and restraint: finish the work without approving or notifying twice.

The [full demonstration](https://raw.githubusercontent.com/kabbersokhi-boop/agents-should-survive-failure/57cd96e6554399c4002cc14fcb2320c0e50c4fad/docs/evidence/portfolio/demo/supplier-crash-recovery.mp4) follows
one fictional supplier, **Orion Payments**, through that boundary. It shows real
application interactions, native Temporal history, native Grafana metrics and an
explicitly labelled read-only local crash monitor. No desktop, browser chrome,
personal account, credential or live customer data appears in the footage.

## The story in six steps

1. **0:09 · Create the case.** An authenticated operator enters the supplier, jurisdiction,
   unique reference and fictional contact address. The API persists the case.
2. **0:29 · Understand the recommendation.** Deterministic jurisdiction policy produces a
   risk score of 65. The console presents retrieved policy citations and a bounded
   advisory explanation. The model has no approval authority.
3. **0:56 · Make an accountable decision.** The workflow waits durably for an authorized
   operator. Approval targets the exact request and version, with a rationale and
   idempotency key. The three business-effect counts start at zero.
4. **1:06 · Fail at the difficult boundary.** A case-scoped development fault pauses the
   activity after its effects commit but before acknowledgement. The local harness
   verifies one of each effect and Temporal `RUNNING`, then kills the inspected
   worker. The monitor records exit code 137 from `SIGKILL`.
5. **1:48 · Resume without starting over.** The harness starts the same existing container,
   preserving its configuration and volumes, and observes a different process ID.
   Temporal redelivers the decision activity. Native history shows attempt 2 and
   workflow completion.
6. **2:29 · Prove the business outcome.** The same case remains at one approval decision,
   one approved-supplier record and one synthetic notification. Grafana corroborates
   redelivery and duplicate prevention; its aggregate counters are not the
   case-specific proof.

## Inspect the evidence

| Evidence | What it establishes |
| --- | --- |
| [Before acknowledgement](evidence/portfolio/demo/02-committed-before-ack.png) | Database effects are committed while Temporal is still `RUNNING` |
| [Observed worker crash](evidence/portfolio/demo/03-observed-worker-crash.png) | The local harness observes process termination, not just a simulated error message |
| [Native Temporal history](evidence/portfolio/demo/04-temporal-redelivery.png) | The selected workflow completes after activity redelivery |
| [Grafana dashboard](evidence/portfolio/demo/05-grafana-operations.png) | Aggregate operational context and replacement-worker health |
| [Recovered case](evidence/portfolio/demo/06-recovered-case.png) | Case-scoped counts remain one, with independently observed Temporal completion |
| [Recorded recovery proof](evidence/portfolio/demo/recovery-proof.json) | Timestamped observations, run identity, process IDs, exit code, attempts and counts |
| [Evaluation snapshot](evidence/portfolio/demo/evaluation.json) | 24 of 24 reference cases passed against real Temporal workflows in a local development environment |
| [Media inventory](evidence/portfolio/demo/verification.json) | Asset sizes, SHA-256 hashes, recording scope and media metadata |
| [Chapter timings](evidence/portfolio/demo/chapters.json) | The edited video's case identity and chapter boundaries |

The evaluation suite covers a separate set of cases; it is not substituted for
the recorded case's recovery proof. Historical release evidence remains in the
[v0.2.0 evidence index](evidence/v0.2.0.md).

## What this demonstration does—and does not—claim

- **Real failure:** the harness sends `SIGKILL` to a development worker. Browser
  clients never receive Docker authority, and application settings reject fault
  injection in production.
- **Separate truths:** PostgreSQL business state and Temporal execution state are
  observed separately. A committed outcome is not presented as workflow completion.
- **Bounded AI:** this recording uses the deterministic advisory provider, not live
  NVIDIA inference. Policy and authenticated approval remain authoritative.
- **Synthetic effects:** the supplier is fictional and the notification is a local
  database record. No external email delivery is demonstrated.
- **Honest editing:** chapter captions, shortened waits and freeze holds improve
  readability. The displayed application states and recovery observations are real.
- **Scoped guarantee:** at-least-once execution produces duplicate-safe local
  business effects for the tested workflow. This is not exactly-once distributed
  execution or proof that arbitrary external services are duplicate-safe.

## Reproduce and verify

Follow the [local operator-demo guide](operator-demo.md) to create an isolated
environment, provision a scoped session, submit a supplier and run the controlled
crash. The harness will produce new case-specific evidence; identifiers and
timestamps will differ from the published snapshot.

```bash
npm run check
make lint typecheck test test-security dependency-audit
```

The dependency-free media verifier checks published asset hashes, case identity,
ordered crash/restart observations, different worker process IDs, actual Temporal
redelivery, one of each persisted effect and the 24-case evaluation snapshot.
It checks the captured evidence's consistency, not the state of a running deployment.
