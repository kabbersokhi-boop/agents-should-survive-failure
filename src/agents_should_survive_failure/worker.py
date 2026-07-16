"""Temporal worker for durable vendor-onboarding workflows."""

import asyncio
import signal
from contextlib import suppress

import structlog
from prometheus_client import start_http_server
from temporalio.client import Client
from temporalio.contrib.opentelemetry import TracingInterceptor
from temporalio.worker import Worker

from agents_should_survive_failure.dependencies import create_resources
from agents_should_survive_failure.fault_injection import FaultInjector
from agents_should_survive_failure.mcp_adapter import GovernedMCPAdapter
from agents_should_survive_failure.metrics import WORKER_STARTS
from agents_should_survive_failure.observability import configure_logging, configure_trace_provider
from agents_should_survive_failure.persistence.session import Database
from agents_should_survive_failure.policy import PolicyRetriever
from agents_should_survive_failure.provider_factory import (
    build_embedding_provider,
    build_model_provider,
)
from agents_should_survive_failure.settings import get_settings
from agents_should_survive_failure.tool_gateway import ToolGateway
from agents_should_survive_failure.workflows.activities import VendorOnboardingActivities
from agents_should_survive_failure.workflows.contracts import TASK_QUEUE
from agents_should_survive_failure.workflows.managed_activities import ManagedAgentActivities
from agents_should_survive_failure.workflows.managed_agent import ManagedAgentWorkflow
from agents_should_survive_failure.workflows.vendor_onboarding import VendorOnboardingWorkflow


async def run_worker() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    configure_trace_provider(settings)
    if settings.metrics_enabled:
        start_http_server(settings.worker_metrics_port)
    WORKER_STARTS.inc()
    logger = structlog.get_logger(component="worker")
    resources = await create_resources(
        settings, temporal_connect=Client.connect, lazy_temporal_client=False
    )
    try:
        if not await resources.temporal_client.service_client.check_health():
            raise RuntimeError("Temporal workflow service reported unavailable")
        activities = VendorOnboardingActivities(
            Database(resources.engine),
            build_model_provider(settings),
            PolicyRetriever(build_embedding_provider(settings)),
            GovernedMCPAdapter(ToolGateway(Database(resources.engine))),
            FaultInjector(Database(resources.engine), enabled=settings.fault_injection_enabled),
        )
        managed_activities = ManagedAgentActivities(
            Database(resources.engine),
            ToolGateway(Database(resources.engine)),
            build_model_provider(settings),
            resources.temporal_client,
        )
        worker = Worker(
            resources.temporal_client,
            task_queue=TASK_QUEUE,
            interceptors=[TracingInterceptor(always_create_workflow_spans=True)],
            workflows=[VendorOnboardingWorkflow, ManagedAgentWorkflow],
            activities=[
                activities.begin_review,
                activities.assess_risk,
                activities.request_approval,
                activities.record_decision,
                activities.cancel_review,
                managed_activities.execute,
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
