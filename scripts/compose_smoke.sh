#!/usr/bin/env bash
set -euo pipefail

export COMPOSE_PROJECT_NAME=agents-verify
export COMPOSE_PROGRESS=plain
export GIT_COMMIT_SHA="$(git rev-parse HEAD)"

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
uv run python -c 'from agents_should_survive_failure.evaluation_scenarios import validate_packaged_evaluation_suite; validate_packaged_evaluation_suite()'
uv run alembic downgrade -1
uv run alembic upgrade head
uv run python -m agents_should_survive_failure.persistence.cli
export INTEGRATION_API_KEY="$(uv run python -c 'from agents_should_survive_failure.auth_cli import bootstrap_main; bootstrap_main("integration@example.invalid", "Integration Operator", "runs:read,runs:write,approvals:decide,approvals:read,evaluations:read,evaluations:execute,agents:read,agents:write")')"
uv run alembic check
export FAULT_INJECTION_ENABLED=true
docker compose up --build --detach
uv run pytest -m integration tests/integration
if ! evaluation_output="$(docker compose exec -T api /app/.venv/bin/python -c 'from agents_should_survive_failure.persistence.cli import evaluate_main; evaluate_main("release-gate-v1")')"; then
  printf '%s\n' "$evaluation_output" >&2
  docker compose exec -T postgres psql -U temporal -d agents -c "select case_slug, status, summary from evaluation_results where evaluation_run_id = (select id from evaluation_runs where idempotency_key = 'release-gate-v1' order by created_at desc limit 1) order by case_slug;" >&2 || true
  exit 1
fi
printf '%s\n' "$evaluation_output"
evaluation_run_id="$(printf '%s\n' "$evaluation_output" | sed -nE 's/^Evaluation run ([0-9a-f-]+) completed.*/\1/p')"
test -n "$evaluation_run_id"
rm -rf artifacts/evaluations
docker compose exec -T api /app/.venv/bin/python -c "from agents_should_survive_failure.persistence.cli import evaluation_report_main; evaluation_report_main(\"$evaluation_run_id\", \"/tmp/evaluation-reports\")"
mkdir -p artifacts/evaluations
docker compose cp api:/tmp/evaluation-reports/. artifacts/evaluations/
test "$(find artifacts/evaluations -name '*.json' | wc -l)" -eq 1
test "$(find artifacts/evaluations -name '*.md' | wc -l)" -eq 1
bash scripts/test_worker_crash.sh
bash scripts/test_managed_agent.sh
make sbom-backend
make sbom-sdk
make sbom-container
