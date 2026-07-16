"""Centralized, persisted fault injection for isolated tests and evaluations only."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from sqlalchemy import select

from agents_should_survive_failure.failures import FailureCategory, PlatformFailure
from agents_should_survive_failure.persistence.models import (
    FaultInjectionConsumption,
    FaultInjectionPlan,
    FaultPlanStatus,
)
from agents_should_survive_failure.persistence.session import Database


class FaultPoint(StrEnum):
    """Production-owned named fault boundaries; arbitrary fault strings are not supported."""

    VENDOR_LOOKUP = "tool.vendor_database_query.before_execute"
    POLICY_RETRIEVAL = "tool.internal_policy_search.before_execute"
    ACTIVE_ACTIVITY = "worker.active_activity"
    WAITING_FOR_APPROVAL = "worker.waiting_for_approval"
    EMAIL_POST_COMMIT_HANDOFF = "email.send.after_commit_before_ack"
    PROJECTION_POST_COMMIT_HANDOFF = "projection.after_commit_before_ack"
    WORKFLOW_START_HANDOFF = "workflow_start.after_temporal_accept"
    DATABASE_OPERATION = "database.activity_transaction.before_commit"
    PROVIDER_CALL = "provider.risk_explanation.before_execute"
    MCP_CALL = "mcp.call.before_execute"


class FaultAction(StrEnum):
    """Safe fault actions that map to explicit failure classification semantics."""

    DELAY = "delay"
    RETRYABLE_EXCEPTION = "retryable_exception"
    PERMANENT_EXCEPTION = "permanent_exception"
    TEMPORARY_DATABASE_OUTAGE = "temporary_database_outage"
    PROVIDER_FAILURE = "provider_failure"
    TOOL_MCP_FAILURE = "tool_mcp_failure"
    AMBIGUOUS_HANDOFF = "ambiguous_handoff"
    WORKER_TERMINATION = "worker_termination"


class FaultInjectionDisabled(PermissionError):
    """Raised when callers attempt to configure faults outside an isolated environment."""


class WorkerTerminationRequested(PlatformFailure):
    """A coordinated worker-death instruction for an isolated worker harness."""

    def __init__(self) -> None:
        super().__init__(FailureCategory.WORKER_TERMINATED, retryable=True)


@dataclass(frozen=True)
class FaultDirective:
    """A consumed, auditable fault trigger returned exactly once across workers."""

    plan_id: str
    fault_point: FaultPoint
    scope_key: str
    action: FaultAction
    delay_ms: int
    remaining_triggers: int


def failure_for_action(action: FaultAction) -> PlatformFailure:
    failures = {
        FaultAction.RETRYABLE_EXCEPTION: (FailureCategory.TOOL_UNAVAILABLE, True),
        FaultAction.PERMANENT_EXCEPTION: (FailureCategory.INVALID_INPUT, False),
        FaultAction.TEMPORARY_DATABASE_OUTAGE: (FailureCategory.DATABASE_UNAVAILABLE, True),
        FaultAction.PROVIDER_FAILURE: (FailureCategory.PROVIDER_UNAVAILABLE, True),
        FaultAction.TOOL_MCP_FAILURE: (FailureCategory.MCP_UNAVAILABLE, True),
        FaultAction.AMBIGUOUS_HANDOFF: (FailureCategory.AMBIGUOUS_HANDOFF, True),
    }
    category, retryable = failures[action]
    return PlatformFailure(category, retryable=retryable)


class FaultInjector:
    """Persist and atomically consume deterministic test faults across worker replacements."""

    def __init__(self, database: Database, *, enabled: bool) -> None:
        self._database = database
        self._enabled = enabled

    def _require_enabled(self) -> None:
        if not self._enabled:
            raise FaultInjectionDisabled("fault injection is disabled")

    async def create(
        self,
        *,
        fault_point: FaultPoint,
        action: FaultAction,
        scope_key: str,
        trigger_count: int = 1,
        delay_ms: int = 0,
        safe_metadata: dict[str, Any] | None = None,
    ) -> FaultInjectionPlan:
        """Create one active plan; callers must clear a same-scope plan before replacing it."""

        self._require_enabled()
        if not scope_key or len(scope_key) > 240:
            raise ValueError("fault scope key must be between 1 and 240 characters")
        if trigger_count < 1 or trigger_count > 10:
            raise ValueError("fault trigger count must be between 1 and 10")
        if delay_ms < 0 or delay_ms > 60_000:
            raise ValueError("fault delay must be between 0 and 60000 milliseconds")
        metadata = safe_metadata or {}
        if any(
            token in key.lower()
            for key in metadata
            for token in ("secret", "token", "key", "password")
        ):
            raise ValueError("fault metadata may not include secret-like keys")

        async with self._database.session() as session:
            existing = await session.scalar(
                select(FaultInjectionPlan)
                .where(
                    FaultInjectionPlan.fault_point == fault_point.value,
                    FaultInjectionPlan.scope_key == scope_key,
                )
                .with_for_update()
            )
            if existing is not None and existing.status is FaultPlanStatus.ACTIVE:
                raise ValueError("an active fault plan already exists for this point and scope")
            if existing is not None:
                existing.category = action.value
                existing.trigger_count = trigger_count
                existing.remaining_triggers = trigger_count
                existing.delay_ms = delay_ms
                existing.status = FaultPlanStatus.ACTIVE
                existing.safe_metadata = metadata
                return existing
            plan = FaultInjectionPlan(
                fault_point=fault_point.value,
                scope_key=scope_key,
                category=action.value,
                trigger_count=trigger_count,
                remaining_triggers=trigger_count,
                delay_ms=delay_ms,
                status=FaultPlanStatus.ACTIVE,
                safe_metadata=metadata,
            )
            session.add(plan)
            await session.flush()
            return plan

    async def consume(self, *, fault_point: FaultPoint, scope_key: str) -> FaultDirective | None:
        """Atomically consume one trigger, making one-shot plans safe across worker restarts."""

        if not self._enabled:
            return None
        async with self._database.session() as session:
            plan = await session.scalar(
                select(FaultInjectionPlan)
                .where(
                    FaultInjectionPlan.fault_point == fault_point.value,
                    FaultInjectionPlan.scope_key == scope_key,
                    FaultInjectionPlan.status == FaultPlanStatus.ACTIVE,
                    FaultInjectionPlan.remaining_triggers > 0,
                )
                .with_for_update()
            )
            if plan is None:
                return None
            plan.remaining_triggers -= 1
            if plan.remaining_triggers == 0:
                plan.status = FaultPlanStatus.EXHAUSTED
            consumption = FaultInjectionConsumption(
                fault_plan_id=plan.id,
                fault_point=plan.fault_point,
                scope_key=plan.scope_key,
                category=plan.category,
                remaining_triggers=plan.remaining_triggers,
            )
            session.add(consumption)
            return FaultDirective(
                plan_id=str(plan.id),
                fault_point=FaultPoint(plan.fault_point),
                scope_key=plan.scope_key,
                action=FaultAction(plan.category),
                delay_ms=plan.delay_ms,
                remaining_triggers=plan.remaining_triggers,
            )

    async def clear(self, *, fault_point: FaultPoint, scope_key: str) -> bool:
        """Disable a plan without erasing its audit history."""

        self._require_enabled()
        async with self._database.session() as session:
            plan = await session.scalar(
                select(FaultInjectionPlan)
                .where(
                    FaultInjectionPlan.fault_point == fault_point.value,
                    FaultInjectionPlan.scope_key == scope_key,
                )
                .with_for_update()
            )
            if plan is None:
                return False
            plan.remaining_triggers = 0
            plan.status = FaultPlanStatus.CLEARED
            return True

    async def list(self, *, scope_key: str | None = None) -> list[FaultInjectionPlan]:
        """Return plans and state without exposing any sensitive test inputs."""

        self._require_enabled()
        statement = select(FaultInjectionPlan).order_by(
            FaultInjectionPlan.created_at.desc(), FaultInjectionPlan.id.desc()
        )
        if scope_key is not None:
            statement = statement.where(FaultInjectionPlan.scope_key == scope_key)
        async with self._database.session() as session:
            return list((await session.scalars(statement)).all())

    async def inject(self, *, fault_point: FaultPoint, scope_key: str) -> None:
        """Consume and apply a configured action at a named production fault boundary."""

        directive = await self.consume(fault_point=fault_point, scope_key=scope_key)
        if directive is None:
            return
        if directive.delay_ms:
            await asyncio.sleep(directive.delay_ms / 1000)
        if directive.action is FaultAction.DELAY:
            return
        if directive.action is FaultAction.WORKER_TERMINATION:
            raise WorkerTerminationRequested
        raise failure_for_action(directive.action)
