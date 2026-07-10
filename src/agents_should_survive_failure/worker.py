"""Phase 1 worker process that proves Temporal connectivity."""

import asyncio
import signal
from contextlib import suppress

import structlog
from temporalio.client import Client

from agents_should_survive_failure.observability import configure_logging
from agents_should_survive_failure.settings import get_settings


async def run_worker() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    logger = structlog.get_logger(component="worker")
    client = await Client.connect(
        settings.temporal_address,
        namespace=settings.temporal_namespace,
    )
    if not await client.service_client.check_health():
        raise RuntimeError("Temporal workflow service reported unavailable")

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signal_name in (signal.SIGINT, signal.SIGTERM):
        with suppress(NotImplementedError):
            loop.add_signal_handler(signal_name, stop.set)
    logger.info("worker_ready", temporal_namespace=settings.temporal_namespace)
    await stop.wait()
    logger.info("worker_stopped")


def main() -> None:
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
