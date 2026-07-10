import pytest
from httpx import ASGITransport, AsyncClient, Response
from starlette.types import ASGIApp

from agents_should_survive_failure.api import create_app, get_dependencies
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
