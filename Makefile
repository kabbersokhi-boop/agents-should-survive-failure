.DEFAULT_GOAL := help

.PHONY: help setup format lint typecheck test test-unit test-integration verify dev up down compose-check secret-scan migrate downgrade seed reindex-policies evaluate

help:
	@printf '%s\n' 'setup         Install locked development dependencies' \
	  'dev           Run the local API with reload' \
	  'up/down       Start or stop the Phase 0 Compose skeleton' \
	  'format        Format Python source' \
	  'lint          Check formatting and lint rules' \
	  'typecheck     Run strict Pyright checks' \
	  'test          Run unit tests with coverage' \
	  'test-integration Run the isolated Compose smoke gate' \
	  'migrate       Upgrade the application database to head' \
	  'downgrade     Downgrade the application database by one revision' \
	  'seed          Load idempotent local demonstration records' \
	  'reindex-policies Generate configured-provider embeddings for policy documents' \
	  'evaluate      Run deterministic vendor-onboarding behavior evaluations' \
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

compose-check:
	docker compose config --quiet

secret-scan:
	docker run --rm -v "$(CURDIR):/repo" ghcr.io/gitleaks/gitleaks:v8.28.0 detect --config=/repo/.gitleaks.toml --source=/repo --no-git --redact

verify: lint typecheck test compose-check secret-scan test-integration

up:
	docker compose up --build -d

down:
	docker compose down
