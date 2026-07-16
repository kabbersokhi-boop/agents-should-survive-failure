from agents_should_survive_failure.evaluation_reports import render_markdown


def test_markdown_report_is_bounded_and_summarizes_cases() -> None:
    payload = {
        "build": {"package_version": "1.0.0", "commit_sha": "abc123"},
        "evaluation": {
            "status": "succeeded",
            "suite_slug": "vendor-onboarding",
            "suite_version": "1.0.0",
            "suite_schema_version": "1",
            "dataset_sha256": "a" * 64,
        },
        "aggregate": {
            "passed_count": 1,
            "case_count": 1,
            "pass_rate": 1.0,
            "failure_category_counts": {},
            "latency_ms": {"min": 1, "mean": 1, "max": 1},
        },
        "cases": [
            {
                "case_slug": "approved",
                "status": "passed",
                "workflow_run_id": "run-1",
                "duration_ms": 1,
                "retry_count": 0,
                "summary": "Evidence matched.",
            }
        ],
    }

    rendered = render_markdown(payload)

    assert "1/1 passed" in rendered
    assert "approved" in rendered
    assert "chain-of-thought" in rendered
