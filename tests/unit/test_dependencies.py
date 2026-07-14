import asyncio
from typing import cast

import pytest
from temporalio.client import Client

from agents_should_survive_failure.dependencies import (
    DependencySet,
    check_dependencies,
    create_resources,
)
from agents_should_survive_failure.settings import Settings


class HealthyProbe:
    async def check(self) -> None:
        return None


class SlowProbe:
    async def check(self) -> None:
        await asyncio.sleep(1)


class FakeTemporalClient:
    pass


@pytest.mark.asyncio
async def test_dependency_timeout_is_classified() -> None:
    statuses = await check_dependencies(
        DependencySet(postgres=SlowProbe(), temporal=HealthyProbe()),
        timeout_seconds=0.001,
    )

    assert statuses["postgres"].status == "unavailable"
    assert statuses["postgres"].detail == "timeout"
    assert statuses["temporal"].status == "ok"


@pytest.mark.asyncio
async def test_resources_use_lazy_temporal_connection() -> None:
    calls: list[tuple[str, str, bool, int]] = []

    async def connect(
        address: str, *, namespace: str, lazy: bool, interceptors: list[object]
    ) -> Client:
        calls.append((address, namespace, lazy, len(interceptors)))
        return cast(Client, FakeTemporalClient())

    settings = Settings(
        database_url="postgresql+asyncpg://user:password@localhost/database",
        temporal_address="temporal.example:7233",
        temporal_namespace="agents",
    )
    resources = await create_resources(settings, temporal_connect=connect)
    await resources.close()

    assert calls == [("temporal.example:7233", "agents", True, 1)]
