#!/usr/bin/env bash
set -euo pipefail

export COMPOSE_PROJECT_NAME=agents-verify
export COMPOSE_PROGRESS=plain

cleanup() {
  status=$?
  if [[ $status -ne 0 ]]; then
    docker compose ps || true
    docker compose logs --no-color --tail=200 || true
  fi
  docker compose down --volumes --remove-orphans
  return "$status"
}
trap cleanup EXIT

cleanup
docker compose up --detach postgres
until docker compose exec -T postgres pg_isready -U temporal -d temporal >/dev/null 2>&1; do
  sleep 1
done
export DATABASE_URL=postgresql+asyncpg://agents:local-development-only@127.0.0.1:5432/agents
uv run alembic upgrade head
uv run alembic downgrade base
uv run alembic upgrade head
uv run python -m agents_should_survive_failure.persistence.cli
export INTEGRATION_API_KEY="$(uv run python -c 'from agents_should_survive_failure.auth_cli import bootstrap_main; bootstrap_main("integration@example.invalid", "Integration Operator", "runs:read,runs:write,approvals:decide,approvals:read,evaluations:read,agents:read")')"
uv run alembic check
docker compose up --build --detach
uv run pytest -m integration tests/integration
