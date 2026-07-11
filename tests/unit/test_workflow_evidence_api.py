from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import cast
from uuid import UUID

import pytest
from fastapi import HTTPException

from agents_should_survive_failure import api
from agents_should_survive_failure.persistence.models import InvocationStatus
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
