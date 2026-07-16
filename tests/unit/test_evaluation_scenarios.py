import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from agents_should_survive_failure.evaluation_scenarios import (
    EvaluationSuiteDefinition,
    FaultCategory,
    FaultPlan,
    ScenarioType,
    load_evaluation_suite,
    load_packaged_evaluation_suite,
    validate_packaged_evaluation_suite,
)
from agents_should_survive_failure.workflows.contracts import GovernedToolName, WorkflowEventType


def test_packaged_suite_is_reviewed_complete_and_stable() -> None:
    suite = load_packaged_evaluation_suite()
    count, digest = validate_packaged_evaluation_suite()

    assert count == 24
    assert digest == "f8a11accd7b167224c445213218bba7eb324cc457eb0269c3002cdd89f498904"
    assert suite.suite_slug == "vendor-onboarding-phase-b"
    assert suite.suite_version == "1.0.0"
    assert suite.schema_version == "1"
    assert {case.scenario_type for case in suite.cases} == set(ScenarioType)
    assert all(len(suite.case_content_sha256(case)) == 64 for case in suite.cases)
    assert len({suite.case_content_sha256(case) for case in suite.cases}) == 24


def test_packaged_suite_uses_only_persisted_event_and_tool_names() -> None:
    suite = load_packaged_evaluation_suite()

    for case in suite.cases:
        assert set(case.expected_outcome.expected_event_types) <= set(WorkflowEventType)
        assert {item.tool_name for item in case.expected_outcome.tool_invocations} == set(
            GovernedToolName
        )
        assert isinstance(case.driver.approval_attempts, tuple)
        assert isinstance(case.setup.faults, tuple)
        assert isinstance(case.evidence_requirements, tuple)


def test_packaged_suite_is_deeply_immutable() -> None:
    suite = load_packaged_evaluation_suite()
    malformed = next(case for case in suite.cases if case.driver.tool_attempts)
    arguments = malformed.driver.tool_attempts[0].arguments

    with pytest.raises(TypeError, match="does not support item assignment"):
        arguments["external_reference"] = "changed"  # type: ignore[index]

    assert load_packaged_evaluation_suite() is suite
    assert malformed.driver.tool_attempts[0].arguments["external_reference"] == (
        "malformed reference with spaces"
    )


def test_packaged_suite_round_trips_through_strict_json_contract(tmp_path: Path) -> None:
    suite = load_packaged_evaluation_suite()
    path = tmp_path / "suite.json"
    path.write_text(suite.model_dump_json(indent=2), encoding="utf-8")

    loaded = load_evaluation_suite(path)

    assert loaded == suite
    assert loaded.content_sha256() == suite.content_sha256()


def test_suite_rejects_vendor_input_that_production_api_cannot_accept() -> None:
    suite = load_packaged_evaluation_suite()
    payload = suite.model_dump(mode="json")
    payload["cases"][0]["input"]["contact_email"] = "not an email"

    with pytest.raises(ValidationError, match="contact_email"):
        EvaluationSuiteDefinition.model_validate(payload)


def test_suite_rejects_duplicate_case_slugs() -> None:
    suite = load_packaged_evaluation_suite()
    payload = suite.model_dump(mode="json")
    payload["cases"][1]["slug"] = payload["cases"][0]["slug"]

    with pytest.raises(ValidationError, match="slugs must be unique"):
        EvaluationSuiteDefinition.model_validate(payload)


def test_suite_rejects_missing_required_scenario() -> None:
    suite = load_packaged_evaluation_suite()
    payload = suite.model_dump(mode="json")
    payload["cases"] = payload["cases"][:-1]

    with pytest.raises(ValidationError, match="missing required scenarios"):
        EvaluationSuiteDefinition.model_validate(payload)


def test_suite_rejects_unreviewed_extra_fields() -> None:
    suite = load_packaged_evaluation_suite()
    payload = json.loads(suite.model_dump_json())
    payload["cases"][0]["hidden_fault_switch"] = True

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        EvaluationSuiteDefinition.model_validate(payload)


def test_suite_rejects_fictional_workflow_events() -> None:
    suite = load_packaged_evaluation_suite()
    payload = suite.model_dump(mode="json")
    payload["cases"][0]["expected_outcome"]["expected_event_types"][0] = "review.completed"

    with pytest.raises(ValidationError, match="review.completed"):
        EvaluationSuiteDefinition.model_validate(payload)


def test_malformed_input_case_requires_a_real_gateway_probe() -> None:
    suite = load_packaged_evaluation_suite()
    payload = suite.model_dump(mode="json")
    malformed = next(
        case
        for case in payload["cases"]
        if case["scenario_type"] == "malformed_tool_input_rejected"
    )
    malformed["driver"]["tool_attempts"] = []

    with pytest.raises(ValidationError, match="invalid tool probe"):
        EvaluationSuiteDefinition.model_validate(payload)


def test_suite_rejects_internally_inconsistent_terminal_state() -> None:
    suite = load_packaged_evaluation_suite()
    payload = suite.model_dump(mode="json")
    payload["cases"][0]["expected_outcome"]["vendor_status"] = "under_review"
    payload["cases"][0]["expected_outcome"]["approved_vendor_count"] = 0
    payload["cases"][0]["expected_outcome"]["synthetic_email_count"] = 0

    with pytest.raises(ValidationError, match="succeeded runs require an approved vendor"):
        EvaluationSuiteDefinition.model_validate(payload)


def test_suite_requires_every_runtime_evidence_source() -> None:
    suite = load_packaged_evaluation_suite()
    payload = suite.model_dump(mode="json")
    payload["cases"][0]["evidence_requirements"].remove("audit_events")

    with pytest.raises(ValidationError, match="missing evidence sources: audit_events"):
        EvaluationSuiteDefinition.model_validate(payload)


def test_suite_rejects_faults_that_can_repeat_indefinitely() -> None:
    suite = load_packaged_evaluation_suite()
    payload = suite.model_dump(mode="json")
    fault_case = next(case for case in payload["cases"] if case["setup"]["faults"])
    fault_case["setup"]["faults"][0]["consume_once"] = False

    with pytest.raises(ValidationError, match="consume-once semantics"):
        EvaluationSuiteDefinition.model_validate(payload)


@pytest.mark.parametrize(
    "category",
    [FaultCategory.RETRYABLE, FaultCategory.PROCESS_TERMINATION, FaultCategory.AMBIGUOUS_HANDOFF],
)
def test_recovery_faults_require_retryable_semantics(category: FaultCategory) -> None:
    with pytest.raises(ValidationError, match="retryable recovery faults"):
        FaultPlan(
            fault_point="test.fault",
            category=category,
            retryable=False,
        )


def test_permanent_faults_do_not_require_retries() -> None:
    fault = FaultPlan(
        fault_point="test.permanent",
        category=FaultCategory.PERMANENT,
        retryable=False,
    )

    assert fault.expected_retry_count_min == 0


def test_permission_denial_case_must_remove_the_required_immutable_grant() -> None:
    suite = load_packaged_evaluation_suite()
    payload = suite.model_dump(mode="json")
    denied = next(
        case for case in payload["cases"] if case["scenario_type"] == "tool_permission_denied"
    )
    denied["setup"]["omitted_tool_grants"] = []

    with pytest.raises(ValidationError, match="omit the required vendor lookup grant"):
        EvaluationSuiteDefinition.model_validate(payload)


def test_idempotent_replay_must_repeat_the_accepted_decision() -> None:
    suite = load_packaged_evaluation_suite()
    payload = suite.model_dump(mode="json")
    replay = next(
        case for case in payload["cases"] if case["scenario_type"] == "idempotent_decision_replay"
    )
    replay["driver"]["approval_attempts"][1]["decision"] = "rejected"

    with pytest.raises(ValidationError, match="idempotent replay must repeat"):
        EvaluationSuiteDefinition.model_validate(payload)


def test_conflicting_decision_must_disagree_with_the_accepted_decision() -> None:
    suite = load_packaged_evaluation_suite()
    payload = suite.model_dump(mode="json")
    conflict = next(
        case
        for case in payload["cases"]
        if case["scenario_type"] == "conflicting_approval_rejected"
    )
    conflict["driver"]["approval_attempts"][1]["decision"] = "approved"

    with pytest.raises(ValidationError, match="conflict checks must disagree"):
        EvaluationSuiteDefinition.model_validate(payload)


def test_post_decision_check_cannot_precede_the_accepted_decision() -> None:
    suite = load_packaged_evaluation_suite()
    payload = suite.model_dump(mode="json")
    conflict = next(
        case
        for case in payload["cases"]
        if case["scenario_type"] == "conflicting_approval_rejected"
    )
    conflict["driver"]["approval_attempts"].reverse()

    with pytest.raises(ValidationError, match="must follow the accepted decision"):
        EvaluationSuiteDefinition.model_validate(payload)


def test_idempotency_conflict_must_change_the_decision_content() -> None:
    suite = load_packaged_evaluation_suite()
    payload = suite.model_dump(mode="json")
    conflict = next(
        case
        for case in payload["cases"]
        if case["scenario_type"] == "decision_idempotency_conflict"
    )
    conflict["driver"]["approval_attempts"][1]["decision"] = "approved"

    with pytest.raises(ValidationError, match="conflict checks must disagree"):
        EvaluationSuiteDefinition.model_validate(payload)


def test_review_timestamp_requires_timezone_offset() -> None:
    suite = load_packaged_evaluation_suite()
    payload = suite.model_dump(mode="json")
    payload["reviewed_at"] = "2026-07-16T00:00:00"

    with pytest.raises(ValidationError, match="timezone offset"):
        EvaluationSuiteDefinition.model_validate(payload)
