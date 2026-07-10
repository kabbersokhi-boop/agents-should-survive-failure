.DEFAULT_GOAL := help

.PHONY: help setup format lint typecheck test test-unit test-integration verify dev up down compose-check secret-scan

help:
	@printf '%s\n' 'setup         Install locked development dependencies' \
	  'dev           Run the local API with reload' \
	  'up/down       Start or stop the Phase 0 Compose skeleton' \
	  'format        Format Python source' \
	  'lint          Check formatting and lint rules' \
	  'typecheck     Run strict Pyright checks' \
	  'test          Run unit tests with coverage' \
	  'test-integration Run the isolated Compose smoke gate' \
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

compose-check:
	docker compose config --quiet

secret-scan:
	docker run --rm -v "$(CURDIR):/repo" ghcr.io/gitleaks/gitleaks:v8.28.0 detect --source=/repo --no-git --redact

verify: lint typecheck test compose-check secret-scan test-integration

up:
	docker compose up --build -d

down:
	docker compose down
