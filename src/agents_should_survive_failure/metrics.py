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
SANDBOX_EXECUTIONS = Counter(
    "agents_sandbox_executions_total", "Sandbox execution outcomes.", ("outcome",)
)
SANDBOX_DURATION = Histogram(
    "agents_sandbox_duration_seconds", "Sandbox execution duration.", ("outcome",)
)
