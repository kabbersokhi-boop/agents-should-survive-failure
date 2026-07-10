import asyncio
import os
from collections.abc import Awaitable, Callable

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import create_async_engine


async def eventually[T](operation: Callable[[], Awaitable[T]], attempts: int = 60) -> T:
    last_error: BaseException | None = None
    for _ in range(attempts):
        try:
            return await operation()
        except (AssertionError, SQLAlchemyError, httpx.HTTPError) as error:
            last_error = error
            await asyncio.sleep(1)
    raise AssertionError("service did not become ready before timeout") from last_error


@pytest.mark.integration
@pytest.mark.asyncio
async def test_local_infrastructure_stack() -> None:
    async with httpx.AsyncClient(timeout=3) as client:

        async def api_ready() -> dict[str, object]:
            response = await client.get("http://127.0.0.1:8000/health/ready")
            response.raise_for_status()
            payload: dict[str, object] = response.json()
            assert payload["status"] == "ok"
            return payload

        readiness = await eventually(api_ready)
        assert readiness["dependencies"] == {
            "postgres": {"status": "ok", "detail": None},
            "temporal": {"status": "ok", "detail": None},
        }

        async def prometheus_scrapes_api() -> None:
            response = await client.get("http://127.0.0.1:9090/api/v1/targets")
            response.raise_for_status()
            targets = response.json()["data"]["activeTargets"]
            api_targets = [
                target for target in targets if target["labels"].get("job") == "agents-api"
            ]
            assert api_targets and api_targets[0]["health"] == "up"

        await eventually(prometheus_scrapes_api)

        async def grafana_ready() -> None:
            response = await client.get("http://127.0.0.1:3000/api/health")
            response.raise_for_status()
            assert response.json()["database"] == "ok"
            prometheus = await client.get("http://127.0.0.1:3000/api/datasources/uid/prometheus")
            prometheus.raise_for_status()
            assert prometheus.json()["url"] == "http://prometheus:9090"
            tempo = await client.get("http://127.0.0.1:3000/api/datasources/uid/tempo")
            tempo.raise_for_status()
            assert tempo.json()["url"] == "http://tempo:3200"

        await eventually(grafana_ready)

        async def tempo_ready() -> None:
            response = await client.get("http://127.0.0.1:3200/ready")
            response.raise_for_status()

        await eventually(tempo_ready)

        async def api_trace_reaches_tempo() -> None:
            response = await client.get(
                "http://127.0.0.1:3200/api/search",
                params={"q": '{ resource.service.name = "agents-control-plane-api" }'},
            )
            response.raise_for_status()
            assert response.json()["traces"]

        await eventually(api_trace_reaches_tempo)

        async def temporal_ui_ready() -> None:
            response = await client.get("http://127.0.0.1:8080/")
            response.raise_for_status()

        await eventually(temporal_ui_ready)

    async def pgvector_ready() -> str:
        engine = create_async_engine(
            os.getenv(
                "INTEGRATION_DATABASE_URL",
                "postgresql+asyncpg://agents:local-development-only@127.0.0.1:5432/agents",
            )
        )
        try:
            async with engine.connect() as connection:
                result = await connection.execute(
                    text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
                )
                version = result.scalar_one()
                assert isinstance(version, str)
                return version
        finally:
            await engine.dispose()

    assert await eventually(pgvector_ready)
