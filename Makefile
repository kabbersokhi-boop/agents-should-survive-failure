.DEFAULT_GOAL := help

.PHONY: help setup format lint typecheck test test-unit test-integration verify dev up down compose-check secret-scan migrate downgrade seed reindex-policies evaluate nim-smoke-test nim-embedding-smoke-test bootstrap-api-key recover-workflow-starts

help:
	@printf '%s\n' 'setup         Install locked development dependencies' \
	  'dev           Run the local API with reload' \
	  'up/down       Start or stop the Phase 0 Compose skeleton' \
	  'format        Format Python source' \
	  'lint          Check formatting and lint rules' \
	  'typecheck     Run strict Pyright checks' \
	  'test          Run unit tests with coverage' \
	  'test-integration Run the isolated integration and evaluation release gate' \
	  'migrate       Upgrade the application database to head' \
	  'downgrade     Downgrade the application database by one revision' \
	  'seed          Load idempotent local demonstration records' \
	  'reindex-policies Generate configured-provider embeddings for policy documents' \
	  'evaluate      Run deterministic vendor-onboarding behavior evaluations' \
	  'nim-smoke-test Run a manual credential-gated NVIDIA NIM model smoke test' \
	  'nim-embedding-smoke-test Run a manual credential-gated NVIDIA NIM embedding smoke test' \
	  'bootstrap-api-key Create a scoped local API key and print it once' \
	  'recover-workflow-starts Retry persisted workflow starts that need reconciliation' \
	  'secret-scan   Scan tracked content with Gitleaks (Docker)' \
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
	docker compose exec -T api /app/.venv/bin/python -c 'from agents_should_survive_failure.auth_cli import bootstrap_main; bootstrap_main("$(API_KEY_BOOTSTRAP_EMAIL)", "$(API_KEY_BOOTSTRAP_NAME)", "$(API_KEY_BOOTSTRAP_SCOPES)")'

recover-workflow-starts:
	docker compose exec -T api /app/.venv/bin/python -c 'from agents_should_survive_failure.workflow_start_cli import recovery_main; recovery_main()'

compose-check:
	docker compose config --quiet

secret-scan:
	docker run --rm -v "$(CURDIR):/repo" ghcr.io/gitleaks/gitleaks:v8.28.0 detect --config=/repo/.gitleaks.toml --source=/repo --redact

verify: lint typecheck test compose-check secret-scan test-integration

up:
	docker compose up --build -d

down:
	docker compose down
