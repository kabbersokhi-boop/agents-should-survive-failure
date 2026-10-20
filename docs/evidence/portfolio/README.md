# Portfolio evidence

## Project overview

[Watch the 1:33 overview](demo/overview.mp4) · [Subtitles](demo/overview.en.srt) · [Transcript](demo/overview-transcript.txt)

The overview is an edit of the same October 3 supplier recording. It uses enlarged original UI details, narration, one caption band, and editorial diagrams. Its [inventory](demo/overview-verification.json) and [production review](../../demo-production/README.md) identify the output and source recordings. The detailed walkthrough and original recovery proof below are preserved.

## Earlier crash-recovery evidence

These retained screenshots document a separate execution of the reference
crash proof. For the current operator-console journey, use the
[end-to-end video and case-scoped evidence](../../portfolio-demo.md).

### Durable recovery

![Temporal crash recovery](01-temporal-crash-recovery.png)

Temporal redelivers the decision activity after worker termination and completes
the workflow.

### Operational context

![Grafana operations evidence](02-grafana-operations.png)

Grafana shows aggregate recovery, duplicate-prevention and worker-health signals.
These counters do not replace case-scoped database assertions.

### Executable assertions

![Crash-proof terminal](03-crash-proof-terminal.png)

The executable proof checks one approval decision, one vendor projection and
one locally stored synthetic email after worker replacement and redelivery.

## Human authority

![Human approval control plane](04-human-approval-control.png)

The versioned approval contract makes an explicit authenticated decision the
authoritative workflow input.

These are tested local business-effect guarantees under at-least-once execution,
not a claim of exactly-once distributed execution.
