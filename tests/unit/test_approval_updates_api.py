from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID

import pytest
from fastapi import HTTPException, Request
from temporalio.client import WorkflowUpdateFailedError

from agents_should_survive_failure import api
from agents_should_survive_failure.auth import AuthenticatedPrincipal
from agents_should_survive_failure.persistence.models import ApprovalStatus
from agents_should_survive_failure.persistence.session import Database
from agents_should_survive_failure.workflows.contracts import ApprovalDecisionType

RUN_ID = UUID("00000000-0000-0000-0000-000000000020")
APPROVAL_ID = UUID("00000000-0000-0000-0000-000000000021")
PRINCIPAL_ID = UUID("00000000-0000-0000-0000-000000000022")
KEY_ID = UUID("00000000-0000-0000-0000-000000000023")


class Session:
    def __init__(self, values: list[object | None]) -> None:
        self.values = values

    async def scalar(self, statement: object) -> object | None:
        del statement
        return self.values.pop(0)


class FakeDatabase:
    def __init__(self, session: Session) -> None:
        self.session_value = session

    @asynccontextmanager
    async def session(self):  # type: ignore[no-untyped-def]
        yield self.session_value


class Handle:
    def __init__(self, error: BaseException | None = None) -> None:
        self.error = error
        self.calls: list[tuple[str, object, str]] = []

    async def execute_update(self, update: str, decision: object, *, id: str) -> None:
        self.calls.append((update, decision, id))
        if self.error is not None:
            raise self.error


def _run() -> SimpleNamespace:
    return SimpleNamespace(temporal_workflow_id="vendor-onboarding-test")


def _approval() -> SimpleNamespace:
    return SimpleNamespace(id=APPROVAL_ID, status=ApprovalStatus.PENDING, version=1)


def _request(handle: Handle) -> Request:
    def get_workflow_handle(workflow_id: str) -> Handle:
        assert workflow_id == "vendor-onboarding-test"
        return handle

    temporal = SimpleNamespace(get_workflow_handle=get_workflow_handle)
    resources = SimpleNamespace(temporal_client=temporal)
    state = SimpleNamespace(resources=resources)
    return cast(Request, SimpleNamespace(app=SimpleNamespace(state=state)))


def _payload() -> api.ApprovalRequestBody:
    return api.ApprovalRequestBody(
        approval_request_id=APPROVAL_ID,
        expected_version=1,
        decision=ApprovalDecisionType.APPROVED,
        rationale="Synthetic human approval.",
        idempotency_key="approval-update-1",
    )


def _principal() -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(
        id=PRINCIPAL_ID,
        key_id=KEY_ID,
        scopes=frozenset({"approvals:decide"}),
    )


@pytest.mark.asyncio
async def test_approval_endpoint_executes_a_deterministic_workflow_update(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def get_run(self: object, run_id: UUID) -> object | None:
        del self
        assert run_id == RUN_ID
        return _run()

    monkeypatch.setattr(api.WorkflowRunRepository, "get", get_run)
    handle = Handle()
    response = await api.decide_onboarding(
        RUN_ID,
        _payload(),
        _request(handle),
        cast(Database, FakeDatabase(Session([_approval(), None]))),
        _principal(),
    )

    assert response.status_code == 202
    assert handle.calls[0][0] == "decide"
    assert handle.calls[0][2] == "approval-update-1"
    decision = cast(Any, handle.calls[0][1])
    assert decision.decided_by_id == str(PRINCIPAL_ID)
    assert decision.approval_request_id == str(APPROVAL_ID)


@pytest.mark.asyncio
async def test_approval_endpoint_maps_rejected_workflow_update_to_conflict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def get_run(self: object, run_id: UUID) -> object | None:
        del self
        assert run_id == RUN_ID
        return _run()

    monkeypatch.setattr(api.WorkflowRunRepository, "get", get_run)
    with pytest.raises(HTTPException) as raised:
        await api.decide_onboarding(
            RUN_ID,
            _payload(),
            _request(Handle(WorkflowUpdateFailedError(ValueError("stale update")))),
            cast(Database, FakeDatabase(Session([_approval(), None]))),
            _principal(),
        )

    assert raised.value.status_code == 409
    assert raised.value.detail == "approval decision is no longer valid for the workflow state"
