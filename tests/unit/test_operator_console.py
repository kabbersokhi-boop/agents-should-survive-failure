from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any, ClassVar, cast
from uuid import UUID

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
from starlette.requests import Request

from agents_should_survive_failure import api, operator_console
from agents_should_survive_failure.auth import AuthenticatedPrincipal
from agents_should_survive_failure.persistence.models import PrincipalType, RunStatus
from agents_should_survive_failure.persistence.session import Database
from agents_should_survive_failure.settings import Settings

RUN_ID = UUID("00000000-0000-0000-0000-000000000901")


class FakeDatabase:
    row: tuple[object, ...] | None = None
    statements: ClassVar[list[object]] = []

    def __init__(self, engine: object) -> None:
        del engine

    @asynccontextmanager
    async def session(self):  # type: ignore[no-untyped-def]
        yield self

    async def execute(self, statement: object) -> Any:
        self.statements.append(statement)
        return SimpleNamespace(one_or_none=lambda: self.row)

    async def scalars(self, statement: object) -> Any:
        self.statements.append(statement)
        return SimpleNamespace(all=list)


class FakeHandle:
    fail = False

    async def describe(self, **kwargs: object) -> Any:
        del kwargs
        if self.fail:
            raise ConnectionError("private transport detail")
        return SimpleNamespace(
            status=SimpleNamespace(name="RUNNING"),
            raw_description=SimpleNamespace(
                pending_activities=[
                    SimpleNamespace(
                        activity_type=SimpleNamespace(name="vendor_onboarding.record_decision"),
                        attempt=2,
                    )
                ]
            ),
        )

    async def fetch_history(self, **kwargs: object) -> Any:
        del kwargs
        return SimpleNamespace(events=[])


def request(handle: FakeHandle) -> Request:
    def get_handle(identifier: str) -> FakeHandle:
        del identifier
        return handle

    app = api.create_app(Settings())
    app.state.resources = SimpleNamespace(
        engine=None, temporal_client=SimpleNamespace(get_workflow_handle=get_handle)
    )
    return Request({"type": "http", "app": app, "headers": [], "path": "/", "method": "GET"})


@pytest.fixture
def database(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeDatabase.row = (
        SimpleNamespace(id=RUN_ID, status=RunStatus.SUCCEEDED, temporal_workflow_id="onboard-test"),
        SimpleNamespace(legal_name="Synthetic Supplier", jurisdiction="IN", risk_score=40),
        1,
        1,
        1,
    )
    FakeDatabase.statements = []
    monkeypatch.setattr(operator_console, "Database", FakeDatabase)


@pytest.mark.asyncio
async def test_committed_counts_are_not_workflow_completion(database: None) -> None:
    result = await operator_console.case_summary(RUN_ID, request(FakeHandle()))
    assert result.business_status == "succeeded"
    assert result.temporal_status == "RUNNING"
    assert result.decision_activity_attempt == 2
    assert result.effects.model_dump() == {
        "approval_decisions": 1,
        "approved_suppliers": 1,
        "synthetic_notifications": 1,
    }
    assert len(FakeDatabase.statements) == 1


@pytest.mark.asyncio
async def test_unavailable_temporal_preserves_counts_without_leaking_detail(database: None) -> None:
    handle = FakeHandle()
    handle.fail = True
    result = await operator_console.case_summary(RUN_ID, request(handle))
    assert result.temporal_status == "UNAVAILABLE"
    assert result.temporal_observed is False
    assert result.effects.approval_decisions == 1
    assert "private transport" not in result.model_dump_json()


@pytest.mark.asyncio
async def test_unknown_or_non_supplier_case_is_not_found(database: None) -> None:
    FakeDatabase.row = None
    with pytest.raises(HTTPException) as error:
        await operator_console.case_summary(RUN_ID, request(FakeHandle()))
    assert error.value.status_code == 404


def test_counts_are_scoped_to_case_and_use_one_statement() -> None:
    statement = operator_console.case_summary_statement(RUN_ID)
    sql = str(statement.compile())
    assert "approval_requests.workflow_run_id" in sql
    assert "approved_vendors.workflow_run_id" in sql
    assert "synthetic_email_messages.workflow_run_id" in sql
    assert "workflow_runs.workflow_type" in sql
    assert list(statement.compile().params.values()).count(RUN_ID) == 4


@pytest.mark.asyncio
async def test_console_is_public_but_business_evidence_requires_authentication() -> None:
    async with AsyncClient(
        transport=ASGITransport(app=api.create_app()), base_url="http://test"
    ) as client:
        page = await client.get("/console/")
        assert page.status_code == 200
        assert "Supplier operations" in page.text
        assert "frame-ancestors 'none'" in page.headers["content-security-policy"]
        assert page.headers["cache-control"] == "no-store"
        response = await client.get(f"/api/v1/workflow-runs/{RUN_ID}/business-evidence")
        assert response.status_code == 401
        assert response.headers["www-authenticate"] == "Bearer"


@pytest.mark.asyncio
async def test_summary_declares_read_scope_and_no_mutation_route() -> None:
    schema = api.create_app().openapi()
    route = schema["paths"]["/api/v1/workflow-runs/{run_id}/business-evidence"]
    assert set(route) == {"get"}


@pytest.mark.asyncio
async def test_business_evidence_denies_a_key_without_read_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = api.create_app()
    app.dependency_overrides[api.get_authenticated_principal] = lambda: AuthenticatedPrincipal(
        id=RUN_ID,
        key_id=RUN_ID,
        scopes=frozenset({"runs:write"}),
        principal_type=PrincipalType.USER,
    )
    denied: list[tuple[str, ...]] = []

    async def audit(
        request: Request, principal: AuthenticatedPrincipal, scopes: tuple[str, ...]
    ) -> None:
        del request, principal
        denied.append(scopes)

    monkeypatch.setattr(api, "audit_authorization_denial", audit)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(f"/api/v1/workflow-runs/{RUN_ID}/business-evidence")
    assert response.status_code == 403
    assert denied == [("runs:read",)]


@pytest.mark.asyncio
async def test_business_queue_can_exclude_evaluations_without_changing_default_list(
    database: None,
) -> None:
    fake = cast(Database, FakeDatabase(None))
    await api.list_workflow_runs(fake, exclude_evaluations=True)
    await api.list_workflow_runs(fake)
    assert "NOT (EXISTS" in str(FakeDatabase.statements[0])
    assert "evaluation_results.workflow_run_id = workflow_runs.id" in str(
        FakeDatabase.statements[0]
    )
    assert "evaluation_results" not in str(FakeDatabase.statements[1])
