from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import cast
from uuid import uuid4

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy.exc import IntegrityError, OperationalError
from starlette.requests import Request
from starlette.types import ASGIApp, Message

from agents_should_survive_failure import api as api_module
from agents_should_survive_failure.api import (
    RequestBodyLimitMiddleware,
    audit_authorization_denial,
    create_app,
    database_integrity_error,
    database_unavailable_error,
    get_authenticated_principal,
    get_database,
    get_dependencies,
    require_scopes,
)
from agents_should_survive_failure.auth import AuthenticatedPrincipal
from agents_should_survive_failure.dependencies import DependencySet
from agents_should_survive_failure.persistence.models import AuditEvent, PrincipalType
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
async def test_database_errors_use_safe_structured_api_contracts() -> None:
    request = authenticated_request()
    request.state.request_id = "request-123"

    conflict = await database_integrity_error(
        request,
        IntegrityError("insert", {}, Exception("duplicate external reference")),
    )
    unavailable = await database_unavailable_error(
        request,
        OperationalError("connect", {}, Exception("postgres password=not-safe")),
    )

    assert conflict.status_code == 409
    assert conflict.body == (
        b'{"code":"conflict","message":"request conflicts with existing state",'
        b'"request_id":"request-123","field_errors":null}'
    )
    assert unavailable.status_code == 503
    assert b"password" not in unavailable.body


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "status_code", "code"),
    [
        (IntegrityError("insert", {}, Exception("duplicate key SQL text")), 409, "conflict"),
        (
            OperationalError("connect", {}, Exception("postgres://user:password@db.internal")),
            503,
            "dependency_unavailable",
        ),
    ],
)
async def test_vendor_endpoint_maps_database_errors_without_leaking_details(
    monkeypatch: pytest.MonkeyPatch, error: Exception, status_code: int, code: str
) -> None:
    class Session:
        async def __aenter__(self) -> "Session":
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

    class Database:
        def session(self) -> Session:
            return Session()

    class FailingVendors:
        def __init__(self, session: object) -> None:
            del session

        async def add(self, vendor: object) -> object:
            del vendor
            raise error

    app = create_app()
    app.dependency_overrides[get_database] = lambda: Database()
    app.dependency_overrides[get_authenticated_principal] = lambda: AuthenticatedPrincipal(
        uuid4(), uuid4(), frozenset({"admin"})
    )
    monkeypatch.setattr(api_module, "VendorRepository", FailingVendors)
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
    assert response.status_code == status_code
    assert response.json()["code"] == code
    assert response.json()["request_id"] == "request-123"
    for forbidden in ("sql", "password", "db.internal", "postgres", "asyncpg"):
        assert forbidden not in response.text.lower()


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


@pytest.mark.asyncio
async def test_request_limit_rejects_chunked_body_without_content_length() -> None:
    app = create_app(Settings(max_request_body_bytes=1_024))
    sent: list[Message] = []
    messages: list[Message] = [
        {
            "type": "http.request",
            "body": b"x" * 1_025,
            "more_body": False,
        }
    ]

    async def receive() -> Message:
        return messages.pop(0) if messages else {"type": "http.disconnect"}

    async def send(message: Message) -> None:
        sent.append(message)

    await app(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/api/v1/vendors",
            "raw_path": b"/api/v1/vendors",
            "query_string": b"",
            "headers": [(b"x-request-id", b"chunked-request")],
            "client": ("127.0.0.1", 1234),
            "server": ("testserver", 80),
        },
        receive,
        send,
    )

    response_start = next(message for message in sent if message["type"] == "http.response.start")
    assert cast(int, response_start["status"]) == 413
    body = b"".join(
        cast(bytes, message["body"])
        for message in sent
        if message["type"] == "http.response.body" and "body" in message
    )
    assert b'"code":"payload_too_large"' in body


@pytest.mark.asyncio
async def test_request_limit_does_not_wrap_streaming_get_requests() -> None:
    messages: list[Message] = [
        {"type": "http.request", "body": b"", "more_body": False},
    ]
    received: list[Message] = []

    async def receive() -> Message:
        return messages.pop(0) if messages else {"type": "http.disconnect"}

    async def send(message: Message) -> None:
        del message

    async def streaming_app(scope: object, replay_receive: object, send_: object) -> None:
        del scope, send_
        replay = cast(Callable[[], Awaitable[Message]], replay_receive)
        received.append(await replay())
        received.append(await replay())

    middleware = RequestBodyLimitMiddleware(cast(ASGIApp, streaming_app), max_body_bytes=1_024)
    await middleware(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/events",
            "raw_path": b"/events",
            "query_string": b"",
            "headers": [],
            "client": ("127.0.0.1", 1234),
            "server": ("testserver", 80),
        },
        receive,
        send,
    )

    assert [message["type"] for message in received] == ["http.request", "http.disconnect"]


def test_versioned_read_contracts_are_exposed_with_deprecated_status_alias() -> None:
    schema = create_app().openapi()

    assert "/api/v1/workflow-runs" in schema["paths"]
    assert "/api/v1/workflow-runs/{run_id}/events" in schema["paths"]
    assert "/api/v1/workflow-runs/{run_id}/approvals" in schema["paths"]
    assert "/api/v1/workflow-runs/{run_id}/model-calls" in schema["paths"]
    assert "/api/v1/workflow-runs/{run_id}/tool-calls" in schema["paths"]
    assert "/api/v1/workflow-runs/{run_id}/checkpoints" in schema["paths"]
    assert "/api/v1/workflow-runs/{run_id}/artifacts" in schema["paths"]
    assert "/api/v1/workflow-runs/{run_id}/artifacts/{artifact_id}" in schema["paths"]
    assert "/api/v1/workflow-runs/{run_id}/budget" in schema["paths"]
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
    request = authenticated_request()
    with pytest.raises(HTTPException) as denied:
        await scope_dependency(
            request,
            AuthenticatedPrincipal(uuid4(), uuid4(), frozenset({"runs:read"})),
        )
    assert denied.value.status_code == 403

    principal = AuthenticatedPrincipal(uuid4(), uuid4(), frozenset({"admin"}))
    assert await scope_dependency(request, principal) is principal


@pytest.mark.asyncio
async def test_approval_scope_rejects_agent_principals() -> None:
    scope_dependency = require_scopes(
        "approvals:decide",
        allowed_principal_types=frozenset({PrincipalType.USER, PrincipalType.SERVICE}),
    )
    agent = AuthenticatedPrincipal(
        uuid4(),
        uuid4(),
        frozenset({"approvals:decide"}),
        principal_type=PrincipalType.AGENT,
    )

    with pytest.raises(HTTPException) as denied:
        await scope_dependency(authenticated_request(), agent)

    assert denied.value.status_code == 403


@pytest.mark.asyncio
async def test_authorization_denial_is_audited_without_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    principal = AuthenticatedPrincipal(uuid4(), uuid4(), frozenset({"runs:read"}))
    request = authenticated_request()
    request.scope["app"] = SimpleNamespace(
        state=SimpleNamespace(resources=SimpleNamespace(engine=object()))
    )
    recorded: list[object] = []

    class Audits:
        def __init__(self, session: object) -> None:
            del session

        async def append(self, event: object) -> None:
            recorded.append(event)

    def database_factory(engine: object) -> FakeAuthDatabase:
        del engine
        return FakeAuthDatabase()

    monkeypatch.setattr("agents_should_survive_failure.api.Database", database_factory)
    monkeypatch.setattr("agents_should_survive_failure.api.AuditEventRepository", Audits)

    await audit_authorization_denial(request, principal, ("approvals:decide",))

    assert len(recorded) == 1
    event = recorded[0]
    assert isinstance(event, AuditEvent)
    assert event.actor_id == principal.id
    assert event.action == "api.authorization.denied"
    assert event.evidence == {
        "route": "unmatched",
        "required_scopes": ["approvals:decide"],
        "principal_type": "user",
    }
