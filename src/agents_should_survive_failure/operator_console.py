"""Read-only, case-scoped business evidence and bounded Temporal observations."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.sql import Select

from agents_should_survive_failure.dependencies import RuntimeResources
from agents_should_survive_failure.persistence.models import (
    ApprovalDecision,
    ApprovalRequest,
    ApprovedVendor,
    SyntheticEmailMessage,
    Vendor,
    WorkflowRun,
)
from agents_should_survive_failure.persistence.session import Database


class EffectCounts(BaseModel):
    approval_decisions: int = Field(ge=0)
    approved_suppliers: int = Field(ge=0)
    synthetic_notifications: int = Field(ge=0)


class CaseSummary(BaseModel):
    run_id: UUID
    supplier_name: str
    jurisdiction: str
    risk_score: int | None
    business_status: str
    temporal_workflow_id: str
    temporal_namespace: str
    temporal_status: str
    temporal_observed: bool
    decision_activity_attempt: int | None
    effects: EffectCounts
    observed_at: datetime


def case_summary_statement(run_id: UUID) -> Select[tuple[WorkflowRun, Vendor, int, int, int]]:
    """All three effect counts share one PostgreSQL statement snapshot."""
    decisions = (
        select(func.count())
        .select_from(ApprovalDecision)
        .join(ApprovalRequest)
        .where(ApprovalRequest.workflow_run_id == run_id)
        .scalar_subquery()
    )
    suppliers = (
        select(func.count())
        .select_from(ApprovedVendor)
        .where(ApprovedVendor.workflow_run_id == run_id)
        .scalar_subquery()
    )
    notifications = (
        select(func.count())
        .select_from(SyntheticEmailMessage)
        .where(SyntheticEmailMessage.workflow_run_id == run_id)
        .scalar_subquery()
    )
    return (
        select(WorkflowRun, Vendor, decisions, suppliers, notifications)
        .join(Vendor, Vendor.id == WorkflowRun.vendor_id)
        .where(WorkflowRun.id == run_id, WorkflowRun.workflow_type == "vendor_onboarding")
    )


async def case_summary(run_id: UUID, request: Request) -> CaseSummary:
    resources: RuntimeResources = request.app.state.resources
    async with Database(resources.engine).session() as session:
        row = (await session.execute(case_summary_statement(run_id))).one_or_none()
        if row is None:
            raise HTTPException(status_code=404, detail="supplier onboarding case not found")
        run, vendor, decisions, suppliers, notifications = row
        result = CaseSummary(
            run_id=run.id,
            supplier_name=vendor.legal_name,
            jurisdiction=vendor.jurisdiction,
            risk_score=vendor.risk_score,
            business_status=run.status.value,
            temporal_workflow_id=run.temporal_workflow_id,
            temporal_namespace=request.app.state.settings.temporal_namespace,
            temporal_status="UNKNOWN",
            temporal_observed=False,
            decision_activity_attempt=None,
            effects=EffectCounts(
                approval_decisions=decisions,
                approved_suppliers=suppliers,
                synthetic_notifications=notifications,
            ),
            observed_at=datetime.now(UTC),
        )
    # Never infer orchestration completion from a successful database business status.
    try:
        async with asyncio.timeout(request.app.state.settings.dependency_timeout_seconds):
            handle = resources.temporal_client.get_workflow_handle(result.temporal_workflow_id)
            description = await handle.describe(rpc_timeout=timedelta(seconds=3))
            history = await handle.fetch_history(rpc_timeout=timedelta(seconds=3))
        scheduled = {
            event.event_id
            for event in history.events
            if event.HasField("activity_task_scheduled_event_attributes")
            and event.activity_task_scheduled_event_attributes.activity_type.name
            == "vendor_onboarding.record_decision"
        }
        attempts = [
            event.activity_task_started_event_attributes.attempt
            for event in history.events
            if event.HasField("activity_task_started_event_attributes")
            and event.activity_task_started_event_attributes.scheduled_event_id in scheduled
        ]
        attempts.extend(
            activity.attempt
            for activity in description.raw_description.pending_activities
            if activity.activity_type.name == "vendor_onboarding.record_decision"
        )
        result.temporal_status = description.status.name if description.status else "UNKNOWN"
        result.temporal_observed = description.status is not None
        result.decision_activity_attempt = max(attempts) if attempts else None
    except Exception:
        # Preserve business evidence without leaking transport details or inventing recovery.
        result.temporal_status = "UNAVAILABLE"
    return result
