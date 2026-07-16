"""Safety and classification checks for centralized test fault injection."""

from typing import cast

import pytest

from agents_should_survive_failure.failures import FailureCategory
from agents_should_survive_failure.fault_injection import (
    FaultAction,
    FaultInjectionDisabled,
    FaultInjector,
    FaultPoint,
    WorkerTerminationRequested,
    failure_for_action,
)
from agents_should_survive_failure.persistence.session import Database
from agents_should_survive_failure.settings import Settings


def test_production_settings_reject_fault_injection() -> None:
    with pytest.raises(ValueError, match="cannot be enabled in production"):
        Settings(app_env="production", fault_injection_enabled=True)


@pytest.mark.asyncio
async def test_disabled_injector_rejects_plan_management_and_is_inert() -> None:
    injector = FaultInjector(cast(Database, object()), enabled=False)

    with pytest.raises(FaultInjectionDisabled):
        await injector.create(
            fault_point=FaultPoint.VENDOR_LOOKUP,
            action=FaultAction.RETRYABLE_EXCEPTION,
            scope_key="run-1",
        )
    assert await injector.consume(fault_point=FaultPoint.VENDOR_LOOKUP, scope_key="run-1") is None


@pytest.mark.parametrize(
    ("action", "category", "retryable"),
    [
        (FaultAction.RETRYABLE_EXCEPTION, FailureCategory.TOOL_UNAVAILABLE, True),
        (FaultAction.TEMPORARY_DATABASE_OUTAGE, FailureCategory.DATABASE_UNAVAILABLE, True),
        (FaultAction.PROVIDER_FAILURE, FailureCategory.PROVIDER_UNAVAILABLE, True),
        (FaultAction.TOOL_MCP_FAILURE, FailureCategory.MCP_UNAVAILABLE, True),
        (FaultAction.AMBIGUOUS_HANDOFF, FailureCategory.AMBIGUOUS_HANDOFF, True),
        (FaultAction.PERMANENT_EXCEPTION, FailureCategory.INVALID_INPUT, False),
    ],
)
def test_fault_actions_use_explicit_retry_classification(
    action: FaultAction, category: FailureCategory, retryable: bool
) -> None:
    failure = failure_for_action(action)

    assert failure.category is category
    assert failure.retryable is retryable


def test_worker_termination_is_a_retryable_coordinated_failure() -> None:
    failure = WorkerTerminationRequested()

    assert failure.category is FailureCategory.WORKER_TERMINATED
    assert failure.retryable is True
