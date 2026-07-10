import asyncio
from typing import cast

import pytest
from temporalio.client import Client

from agents_should_survive_failure import worker


class FakeServiceClient:
    async def check_health(self) -> bool:
        return True


class FakeTemporalClient:
    service_client = FakeServiceClient()


class ReadyEvent:
    def set(self) -> None:
        return None

    async def wait(self) -> bool:
        await asyncio.sleep(0)
        return True


@pytest.mark.asyncio
async def test_worker_checks_temporal_before_becoming_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def connect(*args: object, **kwargs: object) -> Client:
        return cast(Client, FakeTemporalClient())

    monkeypatch.setattr(worker.Client, "connect", connect)
    monkeypatch.setattr(worker.asyncio, "Event", ReadyEvent)

    await worker.run_worker()
