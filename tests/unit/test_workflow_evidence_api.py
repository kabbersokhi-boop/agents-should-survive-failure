from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import cast
from uuid import UUID

import pytest
from fastapi import HTTPException, Request

from agents_should_survive_failure import api
from agents_should_survive_failure.auth import AuthenticatedPrincipal
from agents_should_survive_failure.persistence.models import InvocationStatus, PrincipalType
from agents_should_survive_failure.persistence.session import Database

RUN_ID = UUID("00000000-0000-0000-0000-000000000010")


class FakeScalarResult:
    def __init__(self, values: list[object]) -> None:
        self._values = values

    def all(self) -> list[object]:
        return self._values


class FakeSession:
    def __init__(self, scalar_results: list[FakeScalarResult]) -> None:
        self._scalar_results = scalar_results

    async def scalars(self, statement: object) -> FakeScalarResult:
        del statement
        return self._scalar_results.pop(0)


class FakeDatabase:
    def __init__(self, session: FakeSession) -> None:
        self._session = session

    @asynccontextmanager
    async def session(self):  # type: ignore[no-untyped-def]
        yield self._session


@pytest.mark.asyncio
async def test_workflow_evidence_returns_events_and_bounded_model_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    event = SimpleNamespace(
        sequence=25,
        event_type="risk.policy_context",
        summary="Risk explanation grounded in retrieved policy evidence.",
        payload={"citations": [{"source_uri": "policy://vendor-approval"}]},
    )
    model_call = SimpleNamespace(
        provider="deterministic_mock",
        model="deterministic-explainer-v1",
        correlation_id=f"{RUN_ID}:risk-assessment",
        status=InvocationStatus.SUCCEEDED,
        input_tokens=12,
        output_tokens=8,
        latency_ms=1,
        error_category=None,
        decision_summary="Bounded explanation.",
    )
    session = FakeSession([FakeScalarResult([event]), FakeScalarResult([model_call])])

    class Runs:
        async def get(self, run_id: UUID) -> object:
            assert run_id == RUN_ID
            return object()

    def make_runs(database_session: object) -> Runs:
        del database_session
        return Runs()

    monkeypatch.setattr(api, "WorkflowRunRepository", make_runs)
    response = await api.onboarding_evidence(
        RUN_ID,
        cast(Database, FakeDatabase(session)),
    )

    assert response.events[0].payload["citations"]
    assert response.model_calls[0].explanation_summary == "Bounded explanation."
    assert not hasattr(response.model_calls[0], "prompt")


@pytest.mark.asyncio
async def test_workflow_evidence_returns_not_found_for_unknown_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = FakeSession([])

    class Runs:
        async def get(self, run_id: UUID) -> None:
            assert run_id == RUN_ID
            return None

    def make_runs(database_session: object) -> Runs:
        del database_session
        return Runs()

    monkeypatch.setattr(api, "WorkflowRunRepository", make_runs)

    with pytest.raises(HTTPException, match="workflow run not found") as error:
        await api.onboarding_evidence(RUN_ID, cast(Database, FakeDatabase(session)))

    assert error.value.status_code == 404


@pytest.mark.asyncio
async def test_execute_evaluation_preserves_uuid_principal_and_suite_provenance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    principal = AuthenticatedPrincipal(
        id=UUID("00000000-0000-0000-0000-000000000011"),
        key_id=UUID("00000000-0000-0000-0000-000000000012"),
        scopes=frozenset({"evaluations:execute"}),
        principal_type=PrincipalType.USER,
    )

    class Runner:
        async def run_production_vendor_onboarding(
            self,
            database: object,
            temporal_client: object,
            *,
            requested_by_id: UUID,
            idempotency_key: str,
            fault_injection_enabled: bool,
        ) -> SimpleNamespace:
            assert isinstance(database, FakeDatabase)
            assert temporal_client == "temporal-client"
            assert requested_by_id == principal.id
            assert idempotency_key == "phase-b1-unit"
            assert fault_injection_enabled is True
            return SimpleNamespace(
                id=RUN_ID,
                suite_slug="vendor-onboarding-phase-b",
                suite_version="1.0.0",
                suite_schema_version="1",
                dataset_sha256="a" * 64,
                status=SimpleNamespace(value="succeeded"),
                configuration={
                    "execution_mode": "production_temporal_workflow",
                    "workflow_executed": True,
                },
            )

    monkeypatch.setattr(api, "EvaluationRunner", Runner)
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                resources=SimpleNamespace(temporal_client="temporal-client"),
                settings=SimpleNamespace(fault_injection_enabled=True),
            )
        )
    )
    response = await api.execute_evaluation(
        api.EvaluationExecuteRequest(idempotency_key="phase-b1-unit"),
        cast(Request, request),
        cast(Database, FakeDatabase(FakeSession([]))),
        principal,
    )

    assert response.id == RUN_ID
    assert response.suite_slug == "vendor-onboarding-phase-b"
    assert response.configuration["workflow_executed"] is True


@pytest.mark.asyncio
async def test_evaluation_report_returns_bounded_results(monkeypatch: pytest.MonkeyPatch) -> None:
    del monkeypatch
    run = SimpleNamespace(
        suite_slug="vendor-onboarding-phase-b",
        suite_version="1.0.0",
        suite_schema_version="1",
        dataset_sha256="a" * 64,
        status=SimpleNamespace(value="succeeded"),
        configuration={"provider": "test"},
    )
    result = SimpleNamespace(
        case_slug="low-risk-approved",
        case_version="1",
        case_content_sha256="b" * 64,
        workflow_run_id=None,
        status=SimpleNamespace(value="passed"),
        score=1,
        expected_outcome={"run_status": "succeeded", "risk_score": 25},
        actual_outcome={"catalog_record_valid": True, "workflow_executed": False},
        failure_category=None,
        duration_ms=0,
        metrics={"catalog_record_valid": True, "workflow_executed": False},
        evidence_summary={"workflow_executed": False},
        summary="Expected outcome matched.",
    )
    session = FakeSession([FakeScalarResult([result])])

    async def get(model: object, identifier: UUID) -> object:
        del model
        assert identifier == RUN_ID
        return run

    session.get = get  # type: ignore[attr-defined]
    response = await api.evaluation_report(RUN_ID, cast(Database, FakeDatabase(session)))

    assert response.status == "succeeded"
    assert response.suite_version == "1.0.0"
    assert response.suite_schema_version == "1"
    assert response.results[0].case_slug == "low-risk-approved"
    assert response.results[0].case_content_sha256 == "b" * 64
    assert response.results[0].score == 1.0


@pytest.mark.asyncio
async def test_evaluation_report_returns_not_found_for_unknown_run() -> None:
    session = FakeSession([])

    async def get(model: object, identifier: UUID) -> None:
        del model
        assert identifier == RUN_ID
        return None

    session.get = get  # type: ignore[attr-defined]

    with pytest.raises(HTTPException, match="evaluation run not found") as error:
        await api.evaluation_report(RUN_ID, cast(Database, FakeDatabase(session)))

    assert error.value.status_code == 404
