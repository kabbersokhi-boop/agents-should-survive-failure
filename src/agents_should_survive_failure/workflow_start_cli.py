"""Operator command for reconciling persisted workflow starts."""

import asyncio

from agents_should_survive_failure.dependencies import create_resources
from agents_should_survive_failure.persistence.session import Database
from agents_should_survive_failure.settings import get_settings
from agents_should_survive_failure.workflow_starts import WorkflowStartCoordinator


async def _recover() -> tuple[int, int]:
    resources = await create_resources(get_settings(), lazy_temporal_client=False)
    try:
        result = await WorkflowStartCoordinator(
            Database(resources.engine), resources.temporal_client
        ).recover()
        return result.inspected, result.unavailable
    finally:
        await resources.close()


def recovery_main() -> None:
    inspected, unavailable = asyncio.run(_recover())
    print(f"workflow start recovery: inspected={inspected} unavailable={unavailable}")
    if unavailable:
        raise SystemExit(1)
