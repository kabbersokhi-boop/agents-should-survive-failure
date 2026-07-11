"""Async repositories for core workflow persistence operations."""

import uuid
from collections.abc import Sequence

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from agents_should_survive_failure.persistence.models import (
    AuditEvent,
    RunStatus,
    Vendor,
    VendorStatus,
    WorkflowEvent,
    WorkflowRun,
)


class VendorRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, vendor: Vendor) -> Vendor:
        self._session.add(vendor)
        await self._session.flush()
        return vendor

    async def get(self, vendor_id: uuid.UUID, *, for_update: bool = False) -> Vendor | None:
        statement = select(Vendor).where(Vendor.id == vendor_id)
        if for_update:
            statement = statement.with_for_update()
        return await self._session.scalar(statement)

    async def get_by_external_reference(self, reference: str) -> Vendor | None:
        return await self._session.scalar(
            select(Vendor).where(Vendor.external_reference == reference)
        )

    async def list(
        self, *, status: VendorStatus | None = None, limit: int = 100
    ) -> Sequence[Vendor]:
        statement: Select[tuple[Vendor]] = select(Vendor).order_by(Vendor.created_at, Vendor.id)
        if status is not None:
            statement = statement.where(Vendor.status == status)
        result = await self._session.scalars(statement.limit(limit))
        return result.all()


class WorkflowRunRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, workflow_run: WorkflowRun) -> WorkflowRun:
        self._session.add(workflow_run)
        await self._session.flush()
        return workflow_run

    async def get(self, run_id: uuid.UUID) -> WorkflowRun | None:
        return await self._session.get(WorkflowRun, run_id)

    async def get_by_idempotency_key(self, key: str) -> WorkflowRun | None:
        return await self._session.scalar(
            select(WorkflowRun).where(WorkflowRun.idempotency_key == key)
        )

    async def list(
        self,
        *,
        status: RunStatus | None = None,
        workflow_type: str | None = None,
        limit: int = 100,
    ) -> Sequence[WorkflowRun]:
        statement: Select[tuple[WorkflowRun]] = select(WorkflowRun).order_by(
            WorkflowRun.created_at.desc(), WorkflowRun.id.desc()
        )
        if status is not None:
            statement = statement.where(WorkflowRun.status == status)
        if workflow_type is not None:
            statement = statement.where(WorkflowRun.workflow_type == workflow_type)
        result = await self._session.scalars(statement.limit(limit))
        return result.all()

    async def append_event(self, event: WorkflowEvent) -> WorkflowEvent:
        self._session.add(event)
        await self._session.flush()
        return event

    async def events(self, run_id: uuid.UUID) -> Sequence[WorkflowEvent]:
        result = await self._session.scalars(
            select(WorkflowEvent)
            .where(WorkflowEvent.workflow_run_id == run_id)
            .order_by(WorkflowEvent.sequence)
        )
        return result.all()


class AuditEventRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def append(self, event: AuditEvent) -> AuditEvent:
        self._session.add(event)
        await self._session.flush()
        return event

    async def get_by_idempotency_key(self, key: str) -> AuditEvent | None:
        return await self._session.scalar(
            select(AuditEvent).where(AuditEvent.idempotency_key == key)
        )

    async def for_run(self, run_id: uuid.UUID) -> Sequence[AuditEvent]:
        result = await self._session.scalars(
            select(AuditEvent)
            .where(AuditEvent.workflow_run_id == run_id)
            .order_by(AuditEvent.occurred_at, AuditEvent.id)
        )
        return result.all()
