#!/usr/bin/env bash
set -euo pipefail

PROMETHEUS_URL="${PROMETHEUS_URL:-http://localhost:9090}"
printf '%-24s %s\n' "METRIC" "VALUE"
printf '%-24s %s\n' "token_cost_usd" "$(curl -fsS --get "$PROMETHEUS_URL/api/v1/query" --data-urlencode 'query=sum(workflow_token_cost_usd_total)' | sed -n 's/.*"value":\[[^,]*,"\([^"]*\)".*/\1/p')"
printf '%-24s %s\n' "tool_retries" "$(curl -fsS --get "$PROMETHEUS_URL/api/v1/query" --data-urlencode 'query=sum(workflow_tool_retries_total)' | sed -n 's/.*"value":\[[^,]*,"\([^"]*\)".*/\1/p')"
printf '%-24s %s\n' "approval_wait_seconds" "$(curl -fsS --get "$PROMETHEUS_URL/api/v1/query" --data-urlencode 'query=sum(workflow_approval_wait_seconds_sum)' | sed -n 's/.*"value":\[[^,]*,"\([^"]*\)".*/\1/p')"
