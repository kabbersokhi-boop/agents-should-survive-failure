from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient, Response
from starlette.requests import Request
from starlette.types import ASGIApp

from agents_should_survive_failure.api import (
    create_app,
    get_authenticated_principal,
    get_database,
    get_dependencies,
    require_scopes,
)
from agents_should_survive_failure.auth import AuthenticatedPrincipal
from agents_should_survive_failure.dependencies import DependencySet
from agents_should_survive_failure.settings import Settings


class HealthyProbe:
    async def check(self) -> None:
        return None


class FailingProbe:
    async def check(self) -> None:
        raise ConnectionError("credential-bearing connection text must not be returned")


async def request(app: ASGIApp, path: str, headers: dict[str, str] | None = None) -> Response:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get(path, headers=headers)


async def post(app: ASGIApp, path: str, payload: dict[str, object]) -> Response:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post(path, json=payload, headers={"x-request-id": "request-123"})


@pytest.mark.asyncio
async def test_liveness_contract() -> None:
    response = await request(create_app(), "/health/live", {"x-request-id": "request-123"})

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["x-request-id"] == "request-123"


@pytest.mark.asyncio
async def test_readiness_reports_critical_dependencies() -> None:
    app = create_app(Settings(dependency_timeout_seconds=0.1))
    app.dependency_overrides[get_dependencies] = lambda: DependencySet(
        postgres=HealthyProbe(), temporal=HealthyProbe()
    )

    response = await request(app, "/health/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "dependencies": {
            "postgres": {"status": "ok", "detail": None},
            "temporal": {"status": "ok", "detail": None},
        },
    }


@pytest.mark.asyncio
async def test_readiness_fails_closed_without_leaking_error_text() -> None:
    app = create_app(Settings(dependency_timeout_seconds=0.1))
    app.dependency_overrides[get_dependencies] = lambda: DependencySet(
        postgres=FailingProbe(), temporal=HealthyProbe()
    )

    response = await request(app, "/health/ready")

    assert response.status_code == 503
    assert response.json()["dependencies"]["postgres"] == {
        "status": "unavailable",
        "detail": "ConnectionError",
    }
    assert "credential-bearing" not in response.text


@pytest.mark.asyncio
async def test_prometheus_metrics_are_exposed() -> None:
    app = create_app(Settings(metrics_enabled=True))
    await request(app, "/health/live")

    response = await request(app, "/metrics")

    assert response.status_code == 200
    assert "agents_http_requests_total" in response.text


@pytest.mark.asyncio
async def test_prometheus_metrics_are_disabled_by_default() -> None:
    response = await request(create_app(), "/metrics")

    assert response.status_code == 404
    assert response.json()["code"] == "not_found"


@pytest.mark.asyncio
async def test_versioned_vendor_contract_rejects_unknown_fields() -> None:
    app = create_app()
    app.dependency_overrides[get_database] = lambda: object()
    app.dependency_overrides[get_authenticated_principal] = lambda: AuthenticatedPrincipal(
        uuid4(), uuid4(), frozenset({"admin"})
    )
    response = await post(
        app,
        "/api/v1/vendors",
        {
            "external_reference": "V-100",
            "legal_name": "Vendor",
            "jurisdiction": "US",
            "contact_email": "vendor@example.invalid",
            "unexpected": "rejected",
        },
    )

    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"
    assert response.json()["request_id"] == "request-123"
    assert response.json()["field_errors"][0]["field"] == "body.unexpected"


@pytest.mark.asyncio
async def test_versioned_mutation_requires_authentication_before_database_access() -> None:
    response = await post(
        create_app(),
        "/api/v1/vendors",
        {
            "external_reference": "V-100",
            "legal_name": "Vendor",
            "jurisdiction": "US",
            "contact_email": "vendor@example.invalid",
        },
    )

    assert response.status_code == 401
    assert response.json()["code"] == "authentication_required"


@pytest.mark.asyncio
async def test_versioned_mutation_denies_missing_scope_before_database_access() -> None:
    app = create_app()
    app.dependency_overrides[get_authenticated_principal] = lambda: AuthenticatedPrincipal(
        uuid4(), uuid4(), frozenset({"runs:read"})
    )
    response = await post(
        app,
        "/api/v1/vendors",
        {
            "external_reference": "V-100",
            "legal_name": "Vendor",
            "jurisdiction": "US",
            "contact_email": "vendor@example.invalid",
        },
    )

    assert response.status_code == 403
    assert response.json()["code"] == "authorization_denied"


@pytest.mark.asyncio
async def test_versioned_vendor_contract_rejects_oversized_payload() -> None:
    app = create_app(Settings(max_request_body_bytes=1024))
    app.dependency_overrides[get_database] = lambda: object()
    app.dependency_overrides[get_authenticated_principal] = lambda: AuthenticatedPrincipal(
        uuid4(), uuid4(), frozenset({"admin"})
    )
    response = await post(
        app,
        "/api/v1/vendors",
        {
            "external_reference": "V-100",
            "legal_name": "x" * 2_000,
            "jurisdiction": "US",
            "contact_email": "vendor@example.invalid",
        },
    )

    assert response.status_code == 413
    assert response.json()["code"] == "payload_too_large"


def test_versioned_read_contracts_are_exposed_with_deprecated_status_alias() -> None:
    schema = create_app().openapi()

    assert "/api/v1/workflow-runs" in schema["paths"]
    assert "/api/v1/workflow-runs/{run_id}/events" in schema["paths"]
    assert "/api/v1/workflow-runs/{run_id}/approvals" in schema["paths"]
    assert "/api/v1/workflow-runs/{run_id}/model-calls" in schema["paths"]
    assert "/api/v1/workflow-runs/{run_id}/tool-calls" in schema["paths"]
    assert "/api/v1/agents" in schema["paths"]
    assert "/api/v1/agents/{agent_id}" in schema["paths"]
    assert "/api/v1/events" in schema["paths"]
    assert "/api/v1/events/{event_id}" in schema["paths"]
    assert "/api/v1/approvals" in schema["paths"]
    assert "/api/v1/approvals/{approval_id}" in schema["paths"]
    assert "/api/v1/model-calls" in schema["paths"]
    assert "/api/v1/model-calls/{model_call_id}" in schema["paths"]
    assert "/api/v1/tool-calls" in schema["paths"]
    assert "/api/v1/tool-calls/{tool_call_id}" in schema["paths"]
    assert "/api/v1/evaluations" in schema["paths"]
    assert "/api/v1/evaluations/{evaluation_run_id}" in schema["paths"]
    assert schema["paths"]["/workflow-runs/{run_id}"]["get"]["deprecated"] is True
    approval_schema = schema["components"]["schemas"]["ApprovalRequestBody"]
    assert "decided_by_id" not in approval_schema["properties"]


class FakeAuthDatabase:
    @asynccontextmanager
    async def session(self):  # type: ignore[no-untyped-def]
        yield object()


def authenticated_request(value: str | None = None) -> Request:
    headers = [] if value is None else [(b"authorization", value.encode("ascii"))]
    return Request({"type": "http", "headers": headers})


@pytest.mark.asyncio
async def test_auth_dependency_returns_generic_401_for_missing_or_invalid_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(HTTPException) as missing:
        await get_authenticated_principal(authenticated_request())
    assert missing.value.status_code == 401
    assert missing.value.headers == {"WWW-Authenticate": "Bearer"}

    async def resolve(session: object, token: str) -> None:
        del session, token
        return None

    monkeypatch.setattr("agents_should_survive_failure.api.resolve_api_key", resolve)
    request = authenticated_request("Bearer invalid")
    request.scope["app"] = SimpleNamespace(
        state=SimpleNamespace(resources=SimpleNamespace(engine=object()))
    )

    def database_factory(engine: object) -> FakeAuthDatabase:
        del engine
        return FakeAuthDatabase()

    monkeypatch.setattr("agents_should_survive_failure.api.Database", database_factory)
    with pytest.raises(HTTPException) as invalid:
        await get_authenticated_principal(request)
    assert invalid.value.status_code == 401


@pytest.mark.asyncio
async def test_scope_dependency_denies_and_allows_admin() -> None:
    scope_dependency = require_scopes("approvals:decide")
    with pytest.raises(HTTPException) as denied:
        await scope_dependency(AuthenticatedPrincipal(uuid4(), uuid4(), frozenset({"runs:read"})))
    assert denied.value.status_code == 403

    principal = AuthenticatedPrincipal(uuid4(), uuid4(), frozenset({"admin"}))
    assert await scope_dependency(principal) is principal
