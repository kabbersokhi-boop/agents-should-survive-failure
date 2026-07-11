import pytest
from httpx import ASGITransport, AsyncClient, Response
from starlette.types import ASGIApp

from agents_should_survive_failure.api import create_app, get_database, get_dependencies
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
    app = create_app()
    await request(app, "/health/live")

    response = await request(app, "/metrics")

    assert response.status_code == 200
    assert "agents_http_requests_total" in response.text


@pytest.mark.asyncio
async def test_versioned_vendor_contract_rejects_unknown_fields() -> None:
    app = create_app()
    app.dependency_overrides[get_database] = lambda: object()
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
async def test_versioned_vendor_contract_rejects_oversized_payload() -> None:
    app = create_app(Settings(max_request_body_bytes=1024))
    app.dependency_overrides[get_database] = lambda: object()
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
