"""Temporal worker for durable vendor-onboarding workflows."""

import asyncio
import signal
from contextlib import suppress

import structlog
from temporalio.client import Client
from temporalio.worker import Worker

from agents_should_survive_failure.dependencies import create_resources
from agents_should_survive_failure.observability import configure_logging
from agents_should_survive_failure.persistence.session import Database
from agents_should_survive_failure.settings import get_settings
from agents_should_survive_failure.workflows.activities import VendorOnboardingActivities
from agents_should_survive_failure.workflows.contracts import TASK_QUEUE
from agents_should_survive_failure.workflows.vendor_onboarding import VendorOnboardingWorkflow


async def run_worker() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    logger = structlog.get_logger(component="worker")
    resources = await create_resources(
        settings, temporal_connect=Client.connect, lazy_temporal_client=False
    )
    try:
        if not await resources.temporal_client.service_client.check_health():
            raise RuntimeError("Temporal workflow service reported unavailable")
        activities = VendorOnboardingActivities(Database(resources.engine))
        worker = Worker(
            resources.temporal_client,
            task_queue=TASK_QUEUE,
            workflows=[VendorOnboardingWorkflow],
            activities=[
                activities.begin_review,
                activities.assess_risk,
                activities.request_approval,
                activities.record_decision,
                activities.cancel_review,
            ],
        )
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for signal_name in (signal.SIGINT, signal.SIGTERM):
            with suppress(NotImplementedError):
                loop.add_signal_handler(signal_name, stop.set)
        logger.info(
            "worker_ready", temporal_namespace=settings.temporal_namespace, task_queue=TASK_QUEUE
        )
        async with worker:
            await stop.wait()
        logger.info("worker_stopped")
    finally:
        await resources.close()


def main() -> None:
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
