.DEFAULT_GOAL := help

.PHONY: help setup format lint typecheck test test-unit test-security test-integration dependency-audit sbom-backend sbom-container sbom sdk-build test-sdk-install verify validate-evaluation-dataset dev up down compose-check secret-scan migrate downgrade seed reindex-policies evaluate nim-smoke-test nim-embedding-smoke-test bootstrap-api-key revoke-api-key disable-principal recover-workflow-starts sandbox-demo

help:
	@printf '%s\n' 'setup         Install locked development dependencies' \
	  'dev           Run the local API with reload' \
	  'up/down       Start or stop the Phase 0 Compose skeleton' \
	  'format        Format Python source' \
	  'lint          Check formatting and lint rules' \
	  'typecheck     Run strict Pyright checks' \
	  'test          Run unit tests with coverage' \
	  'test-security Run adversarial tests for implemented security boundaries' \
	  'test-integration Run the isolated integration and evaluation release gate' \
	  'migrate       Upgrade the application database to head' \
	  'downgrade     Downgrade the application database by one revision' \
	  'seed          Load idempotent local demonstration records' \
	  'reindex-policies Generate configured-provider embeddings for policy documents' \
	  'validate-evaluation-dataset Validate the reviewed Phase B evaluation catalog' \
	  'evaluate      Verify B1 catalog persistence integrity (no Temporal execution)' \
	  'nim-smoke-test Run a manual credential-gated NVIDIA NIM model smoke test' \
	  'nim-embedding-smoke-test Run a manual credential-gated NVIDIA NIM embedding smoke test' \
	  'bootstrap-api-key Create a scoped local API key and print it once; supports optional expiry' \
	  'revoke-api-key Revoke an API key by safe key identifier' \
	  'disable-principal Disable a user principal and all of its API access' \
	  'recover-workflow-starts Retry persisted workflow starts that need reconciliation' \
	  'sandbox-demo  Run bounded Python in the local Docker sandbox broker' \
	  'secret-scan   Scan tracked content with Gitleaks (Docker)' \
	  'dependency-audit Scan locked production dependencies with pip-audit' \
	  'sbom          Generate backend and local container CycloneDX SBOM artifacts' \
	  'sdk-build     Build the standalone public SDK wheel and source distribution' \
	  'test-sdk-install Build and install the SDK in a clean Python 3.12 environment' \
	  'verify        Run the complete Phase 0 quality gate'

setup:
	uv sync --frozen --all-groups

dev:
	uv run uvicorn agents_should_survive_failure.api:app --reload

format:
	uv run ruff format .
	uv run ruff check --fix .

lint:
	uv run ruff format --check .
	uv run ruff check .

typecheck:
	uv run pyright

test test-unit:
	uv run coverage run -m pytest tests/unit
	uv run coverage report

test-security:
	uv run pytest -m security tests/security

test-integration:
	bash scripts/compose_smoke.sh

migrate:
	uv run alembic upgrade head

downgrade:
	uv run alembic downgrade -1

seed:
	uv run python -m agents_should_survive_failure.persistence.cli

reindex-policies:
	uv run python -c 'from agents_should_survive_failure.persistence.cli import reindex_main; reindex_main()'

validate-evaluation-dataset:
	uv run python -c 'from agents_should_survive_failure.evaluation_scenarios import validate_packaged_evaluation_suite; count, digest = validate_packaged_evaluation_suite(); print(f"Validated {count} Phase B cases: {digest}")'

evaluate:
	@test -n "$(EVALUATION_IDEMPOTENCY_KEY)" || (echo 'Set EVALUATION_IDEMPOTENCY_KEY to run evaluations.' >&2; exit 2)
	docker compose exec -T api /app/.venv/bin/python -c 'from agents_should_survive_failure.persistence.cli import evaluate_main; evaluate_main("$(EVALUATION_IDEMPOTENCY_KEY)")'

nim-smoke-test:
	uv run python -c 'from agents_should_survive_failure.nim_smoke import model_main; model_main()'

nim-embedding-smoke-test:
	uv run python -c 'from agents_should_survive_failure.nim_smoke import embedding_main; embedding_main()'

bootstrap-api-key:
	@test -n "$(API_KEY_BOOTSTRAP_EMAIL)" || (echo 'Set API_KEY_BOOTSTRAP_EMAIL.' >&2; exit 2)
	@test -n "$(API_KEY_BOOTSTRAP_SCOPES)" || (echo 'Set API_KEY_BOOTSTRAP_SCOPES.' >&2; exit 2)
	docker compose exec -T api /app/.venv/bin/python -c 'from agents_should_survive_failure.auth_cli import bootstrap_main; bootstrap_main("$(API_KEY_BOOTSTRAP_EMAIL)", "$(API_KEY_BOOTSTRAP_NAME)", "$(API_KEY_BOOTSTRAP_SCOPES)", "$(API_KEY_BOOTSTRAP_EXPIRES_AT)")'

revoke-api-key:
	@test -n "$(API_KEY_IDENTIFIER)" || (echo 'Set API_KEY_IDENTIFIER.' >&2; exit 2)
	docker compose exec -T api /app/.venv/bin/python -c 'from agents_should_survive_failure.auth_cli import revoke_main; revoke_main("$(API_KEY_IDENTIFIER)")'

disable-principal:
	@test -n "$(API_KEY_BOOTSTRAP_EMAIL)" || (echo 'Set API_KEY_BOOTSTRAP_EMAIL.' >&2; exit 2)
	docker compose exec -T api /app/.venv/bin/python -c 'from agents_should_survive_failure.auth_cli import disable_principal_main; disable_principal_main("$(API_KEY_BOOTSTRAP_EMAIL)")'

recover-workflow-starts:
	docker compose exec -T api /app/.venv/bin/python -c 'from agents_should_survive_failure.workflow_start_cli import recovery_main; recovery_main()'

sandbox-demo:
	docker compose build api
	uv run python -m agents_should_survive_failure.sandbox_cli

compose-check:
	docker compose config --quiet

secret-scan:
	docker run --rm -v "$(CURDIR):/repo" ghcr.io/gitleaks/gitleaks:v8.28.0 detect --config=/repo/.gitleaks.toml --source=/repo --redact

dependency-audit:
	uv export --frozen --no-dev --no-emit-project --format requirements-txt --output-file /tmp/asf-requirements.txt >/dev/null
	uv run pip-audit --requirement /tmp/asf-requirements.txt --strict

sbom-backend:
	mkdir -p artifacts
	uv run cyclonedx-py environment .venv --output-format json --output-file artifacts/backend.sbom.json

sbom-container:
	mkdir -p artifacts
	docker run --rm -v /var/run/docker.sock:/var/run/docker.sock:ro -v "$(CURDIR)/artifacts:/artifacts" anchore/syft:v1.31.0 agents-control-plane:local -o cyclonedx-json=/artifacts/container.sbom.json

sbom: sbom-backend sbom-container

sdk-build:
	uv build packages/agents-should-survive-failure-sdk

test-sdk-install: sdk-build
	bash scripts/test_sdk_install.sh

verify: lint typecheck validate-evaluation-dataset test test-security compose-check secret-scan dependency-audit test-integration

up:
	docker compose up --build -d

down:
	docker compose down
