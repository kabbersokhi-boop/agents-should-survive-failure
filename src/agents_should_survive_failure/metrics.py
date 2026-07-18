"""Low-cardinality Prometheus metrics shared by API and worker processes."""

from prometheus_client import Counter, Gauge, Histogram

RUN_STARTS = Counter(
    "agents_run_starts_total",
    "Workflow starts handed to Temporal.",
    ("outcome",),
)
RUN_OUTCOMES = Counter(
    "agents_run_terminal_outcomes_total",
    "Terminal workflow outcomes persisted by activities.",
    ("outcome",),
)
ACTIVE_RUNS = Gauge(
    "agents_active_runs",
    "Runs currently observed in an active lifecycle state.",
    ("state",),
)
WORKER_STARTS = Counter("agents_worker_starts_total", "Worker process starts.")
MODEL_CALLS = Counter(
    "agents_model_calls_total",
    "Model calls by provider, model, and outcome.",
    ("provider", "model", "outcome"),
)
MODEL_LATENCY = Histogram(
    "agents_model_latency_seconds",
    "Model call latency.",
    ("provider", "model", "outcome"),
)
MODEL_TOKENS = Counter(
    "agents_model_tokens_total", "Model tokens where reported.", ("provider", "model", "direction")
)
MODEL_ESTIMATED_COST = Counter(
    "agents_model_estimated_cost_usd_total",
    "Estimated model cost where available.",
    ("provider", "model"),
)
EMBEDDING_CALLS = Counter(
    "agents_embedding_calls_total",
    "Embedding calls by provider, model, and outcome.",
    ("provider", "model", "outcome"),
)
EMBEDDING_LATENCY = Histogram(
    "agents_embedding_latency_seconds", "Embedding call latency.", ("provider", "model", "outcome")
)
TOOL_CALLS = Counter(
    "agents_tool_calls_total",
    "Governed tool invocations by registered tool and outcome.",
    ("tool", "version", "outcome"),
)
TOOL_LATENCY = Histogram(
    "agents_tool_latency_seconds",
    "Governed tool invocation latency.",
    ("tool", "version", "outcome"),
)
APPROVAL_REQUESTS = Counter(
    "agents_approval_requests_total", "Approval requests created.", ("outcome",)
)
APPROVAL_DECISIONS = Counter(
    "agents_approval_decisions_total", "Approval decisions persisted.", ("outcome",)
)
APPROVAL_WAIT_DURATION = Histogram(
    "agents_approval_wait_duration_seconds", "Time an approval remained pending.", ("outcome",)
)
ACTIVITY_RETRIES = Counter(
    "agents_activity_retries_total", "Temporal activity retries observed.", ("activity",)
)
DUPLICATE_SIDE_EFFECT_PREVENTED = Counter(
    "agents_duplicate_side_effect_prevented_total",
    "Duplicate business effects prevented.",
    ("effect",),
)
AUTHORIZATION_DENIALS = Counter(
    "agents_authorization_denials_total", "Authenticated authorization denials.", ("route",)
)
EVALUATION_CASES = Counter(
    "agents_evaluation_cases_total", "Evaluation case outcomes.", ("outcome",)
)
SANDBOX_EXECUTIONS = Counter(
    "agents_sandbox_executions_total", "Sandbox execution outcomes.", ("outcome",)
)
SANDBOX_DURATION = Histogram(
    "agents_sandbox_duration_seconds", "Sandbox execution duration.", ("outcome",)
)
WORKFLOW_DURATION = Histogram(
    "workflow_duration_seconds", "End-to-end workflow duration.", ("workflow_type", "status")
)
WORKFLOW_TOKEN_COST = Counter(
    "workflow_token_cost_usd_total", "Estimated model token cost by workflow.", ("workflow_type",)
)
WORKFLOW_TOOL_RETRIES = Counter(
    "workflow_tool_retries_total", "Governed tool retries by workflow.", ("workflow_type", "tool")
)
WORKFLOW_APPROVAL_WAIT = Histogram(
    "workflow_approval_wait_seconds", "Human approval wait time.", ("workflow_type",)
)
