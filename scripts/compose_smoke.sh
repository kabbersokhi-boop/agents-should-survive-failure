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
docker compose up --build --detach
uv run pytest -m integration tests/integration
