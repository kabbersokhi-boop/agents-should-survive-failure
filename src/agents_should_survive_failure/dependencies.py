"""Lifecycle-managed infrastructure dependencies and health checks."""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Protocol

from opentelemetry.instrumentation.sqlalchemy import (  # pyright: ignore[reportMissingTypeStubs]
    SQLAlchemyInstrumentor,
)
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from temporalio.client import Client

from agents_should_survive_failure.settings import Settings


class DependencyProbe(Protocol):
    async def check(self) -> None: ...


class PostgresProbe:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def check(self) -> None:
        async with self._engine.connect() as connection:
            await connection.execute(text("SELECT 1"))


class TemporalProbe:
    def __init__(self, client: Client, timeout_seconds: float) -> None:
        self._client = client
        self._timeout = timedelta(seconds=timeout_seconds)

    async def check(self) -> None:
        healthy = await self._client.service_client.check_health(timeout=self._timeout)
        if not healthy:
            raise RuntimeError("Temporal workflow service reported unavailable")


@dataclass(frozen=True)
class DependencySet:
    postgres: DependencyProbe
    temporal: DependencyProbe


@dataclass(frozen=True)
class DependencyStatus:
    status: str
    detail: str | None = None


async def check_dependencies(
    dependencies: DependencySet,
    timeout_seconds: float,
) -> dict[str, DependencyStatus]:
    async def timed_check(probe: DependencyProbe) -> DependencyStatus:
        try:
            async with asyncio.timeout(timeout_seconds):
                await probe.check()
        except TimeoutError:
            return DependencyStatus(status="unavailable", detail="timeout")
        except Exception as error:  # The readiness boundary maps failures to a safe contract.
            return DependencyStatus(status="unavailable", detail=type(error).__name__)
        return DependencyStatus(status="ok")

    postgres, temporal = await asyncio.gather(
        timed_check(dependencies.postgres),
        timed_check(dependencies.temporal),
    )
    return {"postgres": postgres, "temporal": temporal}


@dataclass
class RuntimeResources:
    engine: AsyncEngine
    temporal_client: Client
    dependencies: DependencySet

    async def close(self) -> None:
        await self.engine.dispose()


async def create_resources(
    settings: Settings,
    temporal_connect: Callable[..., Awaitable[Client]] = Client.connect,
) -> RuntimeResources:
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    SQLAlchemyInstrumentor().instrument(engine=engine.sync_engine)
    try:
        temporal_client = await temporal_connect(
            settings.temporal_address,
            namespace=settings.temporal_namespace,
            lazy=True,
        )
    except BaseException:
        await engine.dispose()
        raise
    return RuntimeResources(
        engine=engine,
        temporal_client=temporal_client,
        dependencies=DependencySet(
            postgres=PostgresProbe(engine),
            temporal=TemporalProbe(temporal_client, settings.dependency_timeout_seconds),
        ),
    )
