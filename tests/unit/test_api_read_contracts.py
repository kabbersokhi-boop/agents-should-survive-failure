from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import cast
from uuid import UUID

import pytest

from agents_should_survive_failure import api
from agents_should_survive_failure.persistence.models import RunStatus
from agents_should_survive_failure.persistence.session import Database

RUN_ID = UUID("00000000-0000-0000-0000-000000000020")
ITEM_ID = UUID("00000000-0000-0000-0000-000000000021")


class Scalars:
    def __init__(self, values: list[object]) -> None:
        self.values = values

    def all(self) -> list[object]:
        return self.values


class Session:
    def __init__(self, result_sets: list[list[object]], model: object | None = None) -> None:
        self.result_sets = result_sets
        self.model = model

    async def scalars(self, statement: object) -> Scalars:
        del statement
        return Scalars(self.result_sets.pop(0))

    async def get(self, model: object, identifier: UUID) -> object | None:
        del model
        assert identifier == RUN_ID
        return self.model


class FakeDatabase:
    def __init__(self, session: Session) -> None:
        self.session_value = session

    @asynccontextmanager
    async def session(self):  # type: ignore[no-untyped-def]
        yield self.session_value


def workflow_run() -> SimpleNamespace:
    return SimpleNamespace(
        id=RUN_ID,
        status=RunStatus.WAITING,
        temporal_workflow_id="workflow-1",
        workflow_type="vendor_onboarding",
        vendor_id=ITEM_ID,
        input_summary={"vendor_id": str(ITEM_ID)},
        result_summary=None,
    )


@pytest.mark.asyncio
async def test_run_and_agent_pages_map_persisted_metadata() -> None:
    run_page = await api.list_workflow_runs(
        cast(Database, FakeDatabase(Session([[workflow_run()]])))
    )
    agent = SimpleNamespace(
        id=ITEM_ID,
        name="vendor-onboarding",
        version="1",
        workflow_type="vendor_onboarding",
        status=SimpleNamespace(value="active"),
        configuration={"durable": True},
    )
    agent_page = await api.list_agents(cast(Database, FakeDatabase(Session([[agent]]))))

    assert run_page.items[0].id == RUN_ID
    assert agent_page.items[0].configuration == {"durable": True}


@pytest.mark.asyncio
async def test_run_read_collections_map_events_approvals_models_and_tools() -> None:
    event = SimpleNamespace(sequence=10, event_type="review.started", summary="Started", payload={})
    approval = SimpleNamespace(
        id=ITEM_ID,
        workflow_run_id=RUN_ID,
        request_key="final-decision",
        status=SimpleNamespace(value="pending"),
        summary="Review",
        version=1,
    )
    model_call = SimpleNamespace(
        id=ITEM_ID,
        workflow_run_id=RUN_ID,
        provider="deterministic_mock",
        model="deterministic-explainer-v1",
        correlation_id="test",
        status=SimpleNamespace(value="succeeded"),
        input_tokens=1,
        output_tokens=1,
        latency_ms=1,
        error_category=None,
        decision_summary="Bounded",
    )
    tool_call = SimpleNamespace(
        id=ITEM_ID,
        workflow_run_id=RUN_ID,
        tool_definition_id=ITEM_ID,
        requested_tool_name="vendor_database_query",
        requested_tool_version="1",
        status=SimpleNamespace(value="succeeded"),
        result_summary={"found": True},
        error_category=None,
    )

    events = await api.list_run_events(
        RUN_ID, cast(Database, FakeDatabase(Session([[event]], model=workflow_run())))
    )
    approvals = await api.list_run_approvals(
        RUN_ID, cast(Database, FakeDatabase(Session([[approval]])))
    )
    models = await api.list_run_model_calls(
        RUN_ID, cast(Database, FakeDatabase(Session([[model_call]])))
    )
    tools = await api.list_run_tool_calls(
        RUN_ID, cast(Database, FakeDatabase(Session([[tool_call]])))
    )

    assert events[0].event_type == "review.started"
    assert approvals[0].status == "pending"
    assert models[0].explanation_summary == "Bounded"
    assert tools[0].result_summary == {"found": True}
