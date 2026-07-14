from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import cast
from uuid import UUID

import pytest
from fastapi import Request

from agents_should_survive_failure import api
from agents_should_survive_failure.auth import AuthenticatedPrincipal
from agents_should_survive_failure.persistence.models import AuditEvent
from agents_should_survive_failure.persistence.session import Database

RUN_ID = UUID("00000000-0000-0000-0000-000000000050")
PRINCIPAL_ID = UUID("00000000-0000-0000-0000-000000000051")
KEY_ID = UUID("00000000-0000-0000-0000-000000000052")


class Session:
    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    async def scalar(self, statement: object) -> None:
        del statement
        return None

    def add(self, event: AuditEvent) -> None:
        self.events.append(event)

    async def flush(self) -> None:
        return None


class FakeDatabase:
    def __init__(self, session: Session) -> None:
        self.session_value = session

    @asynccontextmanager
    async def session(self):  # type: ignore[no-untyped-def]
        yield self.session_value


class Handle:
    def __init__(self) -> None:
        self.signals: list[object] = []

    async def signal(self, signal: object) -> None:
        self.signals.append(signal)


def _request(handle: Handle) -> Request:
    def get_workflow_handle(workflow_id: str) -> Handle:
        assert workflow_id == "vendor-onboarding-test"
        return handle

    temporal = SimpleNamespace(get_workflow_handle=get_workflow_handle)
    resources = SimpleNamespace(temporal_client=temporal)
    state = SimpleNamespace(resources=resources)
    return cast(Request, SimpleNamespace(app=SimpleNamespace(state=state)))


@pytest.mark.asyncio
async def test_cancellation_audits_authenticated_request_before_signal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def get_run(self: object, run_id: UUID) -> object | None:
        del self
        assert run_id == RUN_ID
        return SimpleNamespace(id=RUN_ID, temporal_workflow_id="vendor-onboarding-test")

    monkeypatch.setattr(api.WorkflowRunRepository, "get", get_run)
    session = Session()
    handle = Handle()
    principal = AuthenticatedPrincipal(PRINCIPAL_ID, KEY_ID, frozenset({"runs:write"}))

    response = await api.cancel_onboarding(
        RUN_ID,
        _request(handle),
        cast(Database, FakeDatabase(session)),
        principal,
    )

    assert response.status_code == 202
    assert len(handle.signals) == 1
    assert len(session.events) == 1
    event = session.events[0]
    assert event.action == "api.workflow.cancel.request"
    assert event.actor_id == PRINCIPAL_ID
    assert event.workflow_run_id == RUN_ID
