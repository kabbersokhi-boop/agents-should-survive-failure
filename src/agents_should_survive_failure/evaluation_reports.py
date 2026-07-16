"""Safe, release-oriented JSON and Markdown exports for persisted evaluation runs."""

from __future__ import annotations

import json
import os
import platform
import sys
from collections import Counter
from importlib.metadata import version
from pathlib import Path
from statistics import mean
from typing import Any
from uuid import UUID

from sqlalchemy import select

from agents_should_survive_failure.persistence.models import EvaluationResult, EvaluationRun
from agents_should_survive_failure.persistence.session import Database


class EvaluationReportNotFound(LookupError):
    """The requested persisted evaluation run is not available for export."""


def _build_identifier() -> str:
    return os.environ.get("GIT_COMMIT_SHA", "unknown")[:64]


async def build_report_payload(database: Database, evaluation_run_id: UUID) -> dict[str, Any]:
    """Build a bounded export from persisted evidence without private request payloads."""

    async with database.session() as session:
        run = await session.get(EvaluationRun, evaluation_run_id)
        if run is None:
            raise EvaluationReportNotFound(str(evaluation_run_id))
        results = (
            await session.scalars(
                select(EvaluationResult)
                .where(EvaluationResult.evaluation_run_id == evaluation_run_id)
                .order_by(EvaluationResult.case_slug, EvaluationResult.id)
            )
        ).all()

    cases = [_case_payload(result) for result in results]
    statuses = Counter(case["status"] for case in cases)
    failure_categories = Counter(
        str(case["failure_category"]) for case in cases if case["failure_category"] is not None
    )
    durations = [case["duration_ms"] for case in cases if case["duration_ms"] is not None]
    passed = statuses["passed"]
    return {
        "report_schema_version": "1",
        "build": {
            "commit_sha": _build_identifier(),
            "package_version": version("agents-should-survive-failure"),
            "python_version": sys.version.split()[0],
            "platform": platform.system(),
            "app_environment": os.environ.get("APP_ENV", "unknown"),
        },
        "evaluation": {
            "id": str(run.id),
            "status": run.status.value,
            "suite_slug": run.suite_slug,
            "suite_version": run.suite_version,
            "suite_schema_version": run.suite_schema_version,
            "dataset_sha256": run.dataset_sha256,
            "started_at": run.started_at.isoformat() if run.started_at else None,
            "completed_at": run.completed_at.isoformat() if run.completed_at else None,
            "execution_mode": run.configuration.get("execution_mode"),
        },
        "aggregate": {
            "case_count": len(cases),
            "passed_count": passed,
            "failed_count": statuses["failed"],
            "error_count": statuses["error"],
            "pass_rate": round(passed / len(cases), 4) if cases else 0.0,
            "failure_category_counts": dict(sorted(failure_categories.items())),
            "latency_ms": {
                "min": min(durations) if durations else None,
                "mean": round(mean(durations), 2) if durations else None,
                "max": max(durations) if durations else None,
            },
        },
        "cases": cases,
    }


def _case_payload(result: EvaluationResult) -> dict[str, Any]:
    actual = result.actual_outcome
    metrics = result.metrics
    return {
        "case_slug": result.case_slug,
        "case_version": result.case_version,
        "case_sha256": result.case_content_sha256,
        "workflow_run_id": str(result.workflow_run_id) if result.workflow_run_id else None,
        "status": result.status.value,
        "score": float(result.score),
        "expected_outcome": result.expected_outcome,
        "actual_outcome": actual,
        "failure_category": result.failure_category,
        "duration_ms": result.duration_ms,
        "retry_count": metrics.get("activity_retry_count"),
        "start_attempts": metrics.get("workflow_start_attempts"),
        "approval_result": actual.get("approval_status"),
        "tool_invocations": actual.get("tool_invocations", {}),
        "model_metadata": actual.get("model_metadata", []),
        "projection_count": actual.get("approved_vendor_count"),
        "synthetic_email_count": actual.get("synthetic_email_count"),
        "duplicate_prevention": {
            "approval_decisions": actual.get("approval_decision_count"),
            "workflow_event_sequences": actual.get("workflow_event_sequences", []),
        },
        "evidence_summary": result.evidence_summary,
        "summary": result.summary,
    }


def render_markdown(payload: dict[str, Any]) -> str:
    """Render a concise human report without expanding bounded JSON evidence into secrets."""

    evaluation = payload["evaluation"]
    aggregate = payload["aggregate"]
    lines = [
        "# Evaluation Report",
        "",
        f"- Status: `{evaluation['status']}`",
        f"- Build: `{payload['build']['package_version']}` (`{payload['build']['commit_sha']}`)",
        (
            f"- Suite: `{evaluation['suite_slug']}@{evaluation['suite_version']}` "
            f"(schema `{evaluation['suite_schema_version']}`)"
        ),
        f"- Dataset digest: `{evaluation['dataset_sha256']}`",
        (
            f"- Cases: {aggregate['passed_count']}/{aggregate['case_count']} passed "
            f"({aggregate['pass_rate']:.2%})"
        ),
        "",
        "## Cases",
        "",
        "| Case | Status | Workflow run | Duration (ms) | Retries | Summary |",
        "| --- | --- | --- | ---: | ---: | --- |",
    ]
    for case in payload["cases"]:
        lines.append(
            "| {slug} | {status} | {run_id} | {duration} | {retries} | {summary} |".format(
                slug=case["case_slug"],
                status=case["status"],
                run_id=case["workflow_run_id"] or "-",
                duration=case["duration_ms"] if case["duration_ms"] is not None else "-",
                retries=case["retry_count"] if case["retry_count"] is not None else "-",
                summary=str(case["summary"]).replace("|", "\\|"),
            )
        )
    lines.extend(
        [
            "",
            "## Aggregates",
            "",
            (
                "- Failure categories: "
                f"`{json.dumps(aggregate['failure_category_counts'], sort_keys=True)}`"
            ),
            f"- Latency ms: `{json.dumps(aggregate['latency_ms'], sort_keys=True)}`",
            "",
            "The report intentionally excludes credentials, raw database URLs, prompts, "
            "private tool arguments, and model chain-of-thought.",
        ]
    )
    return "\n".join(lines) + "\n"


async def export_reports(
    database: Database, evaluation_run_id: UUID, output_directory: Path
) -> tuple[Path, Path]:
    """Write deterministic JSON and Markdown artifacts for one persisted evaluation run."""

    payload = await build_report_payload(database, evaluation_run_id)
    output_directory.mkdir(parents=True, exist_ok=True)
    json_path = output_directory / f"evaluation-{evaluation_run_id}.json"
    markdown_path = output_directory / f"evaluation-{evaluation_run_id}.md"
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    markdown_path.write_text(render_markdown(payload), encoding="utf-8")
    return json_path, markdown_path
