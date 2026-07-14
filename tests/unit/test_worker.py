import asyncio
from typing import cast

import pytest
from temporalio.client import Client

from agents_should_survive_failure import worker
from agents_should_survive_failure.settings import Settings


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


class FakeWorker:
    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    async def __aenter__(self) -> "FakeWorker":
        return self

    async def __aexit__(self, *args: object) -> None:
        return None


@pytest.mark.asyncio
async def test_worker_checks_temporal_before_becoming_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def connect(*args: object, **kwargs: object) -> Client:
        return cast(Client, FakeTemporalClient())

    monkeypatch.setattr(worker.Client, "connect", connect)
    monkeypatch.setattr(worker, "Worker", FakeWorker)
    monkeypatch.setattr(worker.asyncio, "Event", ReadyEvent)

    def configure_trace_provider(settings: Settings) -> None:
        del settings

    started_metrics: list[int] = []

    def start_http_server(port: int) -> None:
        started_metrics.append(port)

    settings = Settings(metrics_enabled=True, worker_metrics_port=9100)

    def get_settings() -> Settings:
        return settings

    monkeypatch.setattr(worker, "configure_trace_provider", configure_trace_provider)
    monkeypatch.setattr(worker, "start_http_server", start_http_server)
    monkeypatch.setattr(worker, "get_settings", get_settings)

    await worker.run_worker()

    assert started_metrics == [9100]
