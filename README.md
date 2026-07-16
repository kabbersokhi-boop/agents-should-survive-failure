# Agents Should Survive Failure

Agents Should Survive Failure is a production-style reference implementation for governed,
durable AI workflows. It demonstrates how Temporal, PostgreSQL, idempotent effects, and bounded
model/tool interfaces can keep a consequential workflow correct across retries and worker loss.
It is not a production-ready hosted platform or a compliance product.

## What It Demonstrates

The mature flagship workflow is vendor onboarding. Temporal coordinates review, deterministic risk
assessment, policy retrieval, a human approval update, an approved-vendor projection, and a
synthetic email. PostgreSQL owns durable domain state, append-only audit evidence, idempotency
keys, and run-scoped tool grants.

The reviewed 24-case evaluation suite executes the real Temporal workflow against the Compose
stack. The release gate exports bounded JSON and Markdown evidence from that execution, including
workflow-run IDs and aggregate outcome data. The current release evidence is written to
`artifacts/evaluations/` by the integration gate and attached to the GitHub release.

`make test-worker-crash` performs a separate OS-level proof: it waits until an approved-vendor
projection and synthetic email have committed, kills the real Docker worker with `SIGKILL` during
the configured post-commit acknowledgement delay, starts a replacement worker, and verifies one
approval decision, one projection, one email, unique workflow-event sequences, and stable tool
idempotency.

## Architecture

- Temporal owns workflow execution history, retries, updates, and durable cancellation signals.
- PostgreSQL owns vendors, approvals, projections, synthetic effects, audit records, run state,
  checkpoints, artifacts, budgets, and evaluation evidence.
- Governed local tools enforce run-pinned grants and durable invocation idempotency.
- The model provider is advisory only; deterministic code owns authorization and writes.
- The public SDK defines trusted, operator-installed managed-agent contracts. The generic managed
  runtime executes agent code in a Temporal activity with persisted checkpoints, artifacts,
  budgets, events, and governed tools.

## Quickstart

Prerequisites: Python 3.12, [uv](https://docs.astral.sh/uv/), Docker, and Docker Compose.

```bash
uv python install 3.12
uv sync --frozen --all-groups
make up
curl http://127.0.0.1:8000/health/ready
```

The API is at port 8000 and Temporal UI is at port 8080. `make down` stops the local stack. See
the [local development runbook](docs/runbooks/local-development.md) for the API workflow steps.

## Evaluation And Evidence

```bash
make validate-evaluation-dataset
EVALUATION_IDEMPOTENCY_KEY=local-evaluation make evaluate
EVALUATION_RUN_ID=<evaluation-run-id> make evaluation-report
make test-integration
make test-worker-crash
```

Reports deliberately exclude credentials, database URLs, prompts, private tool arguments, and
model chain-of-thought. Generated evidence is available at
[`artifacts/evaluations/`](artifacts/evaluations/) after `make test-integration`; the release also
attaches the JSON and Markdown files as assets.

## SDK Preview And Operations Agent

The managed-agent SDK/runtime is a preview, not a stable platform contract. Build and validate the
standalone SDK and independently packaged Operations Investigation Agent with:

```bash
make sdk-build test-sdk-install
make external-agent-build test-external-agent
make test-managed-agent
```

The Operations Investigation Agent is a trusted package discovered through the
`agents_should_survive_failure.agents` entry-point group. The production-stack proof registers its
manifest through the authenticated API, starts a real managed Temporal workflow, calls the governed
`internal_policy_search` tool, persists a checkpoint and digest-verified artifact, records budget
consumption and events, and checks the expected output on the non-approval path.

## Verification

`make verify` runs formatting and lint checks, strict Pyright, dataset validation, coverage,
security tests, Compose validation, Gitleaks, dependency audit, migration lifecycle checks,
integration tests, one real evaluation/report export, the worker-kill proof, SDK/package checks,
managed-agent proof, SBOM generation, and cleanup.

NVIDIA NIM live checks remain manual and credential-gated:

```bash
make nim-smoke-test
make nim-embedding-smoke-test
```

## Limitations

- Vendor onboarding is the mature reference workflow; the managed-agent SDK/runtime is preview.
- Delegation code is experimental and is not release-proven.
- External packages are trusted, operator-installed code. Docker is not a complete hostile-code
  isolation boundary.
- NVIDIA NIM live testing requires operator credentials and is not part of public CI.
- This project is neither a real compliance product nor claimed production ready.

See [limitations](docs/limitations.md) and [the threat model](docs/threat-model.md).

## License

Licensed under the Apache License 2.0. See [LICENSE](LICENSE).
