"""Durable, idempotent handoff from control-plane intent to Temporal."""

import hashlib
import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Protocol

from opentelemetry import trace
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from temporalio.exceptions import WorkflowAlreadyStartedError

from agents_should_survive_failure.metrics import RUN_STARTS
from agents_should_survive_failure.persistence.models import (
    AgentToolGrant,
    AuditEvent,
    RunStatus,
    RunToolGrantSnapshot,
    WorkflowRun,
    WorkflowStartAttempt,
    WorkflowStartStatus,
    utc_now,
)
from agents_should_survive_failure.persistence.repositories import AuditEventRepository
from agents_should_survive_failure.persistence.session import Database
from agents_should_survive_failure.workflows.contracts import (
    TASK_QUEUE,
    ManagedAgentInput,
    VendorOnboardingInput,
)
from agents_should_survive_failure.workflows.managed_agent import ManagedAgentWorkflow
from agents_should_survive_failure.workflows.vendor_onboarding import VendorOnboardingWorkflow

START_LEASE = timedelta(seconds=30)


class TemporalWorkflowClient(Protocol):
    async def start_workflow(self, workflow: Any, arg: Any, *, id: str, task_queue: str) -> Any: ...


class RequestFingerprintConflict(Exception):
    """An idempotency key was reused for a different logical request."""


class WorkflowStartUnavailable(Exception):
    """A persisted workflow start could not be handed to Temporal yet."""

    def __init__(self, category: str) -> None:
        super().__init__(category)
        self.category = category


@dataclass(frozen=True)
class StartClaim:
    run: WorkflowRun
    token: uuid.UUID | None

    @property
    def should_start(self) -> bool:
        return self.token is not None


@dataclass(frozen=True)
class RecoveryResult:
    inspected: int
    unavailable: int


def onboarding_request_fingerprint(*, vendor_id: uuid.UUID, agent_id: uuid.UUID) -> str:
    """Hash the semantic request fields, excluding its replay key and caller namespace."""
    payload = {
        "agent_id": str(agent_id),
        "vendor_id": str(vendor_id),
        "workflow_type": "vendor_onboarding",
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def managed_agent_request_fingerprint(*, agent_id: uuid.UUID, task: dict[str, object]) -> str:
    """Hash the complete generic task request before Temporal handoff."""

    payload = {"agent_id": str(agent_id), "task": task, "workflow_type": "managed_agent"}
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )
    return hashlib.sha256(encoded).hexdigest()


def classify_start_error(error: Exception) -> str:
    if isinstance(error, TimeoutError):
        return "timeout"
    name = type(error).__name__.lower()
    if "auth" in name or "permission" in name or "unauth" in name:
        return "authentication"
    if "unavailable" in name or "connection" in name or "transport" in name:
        return "unavailable"
    return "unexpected"


class WorkflowStartCoordinator:
    """Persists intent first and safely reconciles duplicate Temporal start attempts."""

    def __init__(
        self,
        database: Database,
        temporal_client: TemporalWorkflowClient,
        *,
        lease_duration: timedelta = START_LEASE,
        now: Callable[[], Any] = utc_now,
    ) -> None:
        self._database = database
        self._temporal_client = temporal_client
        self._lease_duration = lease_duration
        self._now = now

    async def create_or_get(
        self,
        *,
        vendor_id: uuid.UUID,
        requested_by_id: uuid.UUID,
        agent_id: uuid.UUID,
        idempotency_key: str,
    ) -> WorkflowRun:
        fingerprint = onboarding_request_fingerprint(vendor_id=vendor_id, agent_id=agent_id)
        run_id = uuid.uuid4()
        temporal_workflow_id = f"vendor-onboarding-{run_id}"
        async with self._database.session() as session:
            statement = (
                insert(WorkflowRun)
                .values(
                    id=run_id,
                    agent_id=agent_id,
                    vendor_id=vendor_id,
                    requested_by_id=requested_by_id,
                    workflow_type="vendor_onboarding",
                    temporal_workflow_id=temporal_workflow_id,
                    idempotency_key=idempotency_key,
                    request_fingerprint=fingerprint,
                    status=RunStatus.PENDING,
                    input_summary={"vendor_id": str(vendor_id)},
                    version=1,
                )
                .on_conflict_do_nothing(constraint="uq_workflow_run_principal_idempotency_key")
                .returning(WorkflowRun.id)
            )
            inserted_run_id = (await session.execute(statement)).scalar_one_or_none()
            if inserted_run_id is not None:
                session.add(
                    WorkflowStartAttempt(
                        workflow_run_id=run_id,
                        status=WorkflowStartStatus.PENDING,
                    )
                )
                grants = (
                    await session.scalars(
                        select(AgentToolGrant).where(AgentToolGrant.agent_id == agent_id)
                    )
                ).all()
                for grant in grants:
                    session.add(
                        RunToolGrantSnapshot(
                            workflow_run_id=run_id,
                            tool_definition_id=grant.tool_definition_id,
                            policy_version=grant.policy_version,
                            policy_hash=grant.policy_hash,
                        )
                    )
                await AuditEventRepository(session).append(
                    AuditEvent(
                        workflow_run_id=run_id,
                        actor_id=requested_by_id,
                        action="api.workflow.start.request",
                        resource_type="workflow_run",
                        resource_id=run_id,
                        idempotency_key=f"{run_id}:api.workflow.start.request",
                        summary="Authenticated principal requested a vendor onboarding workflow.",
                        evidence={"vendor_id": str(vendor_id)},
                    )
                )
                run = await session.get(WorkflowRun, run_id)
                assert run is not None
                return run

            run = await session.scalar(
                select(WorkflowRun).where(
                    WorkflowRun.requested_by_id == requested_by_id,
                    WorkflowRun.idempotency_key == idempotency_key,
                )
            )
            if run is None:
                raise RuntimeError("workflow idempotency conflict could not be reloaded")
            if run.request_fingerprint != fingerprint:
                raise RequestFingerprintConflict
            return run

    async def create_or_get_managed_agent(
        self,
        *,
        requested_by_id: uuid.UUID,
        agent_id: uuid.UUID,
        task: dict[str, object],
        idempotency_key: str,
    ) -> WorkflowRun:
        """Persist a generic managed-agent run and immutable tool snapshot before start."""

        fingerprint = managed_agent_request_fingerprint(agent_id=agent_id, task=task)
        run_id = uuid.uuid4()
        temporal_workflow_id = f"managed-agent-{run_id}"
        async with self._database.session() as session:
            statement = (
                insert(WorkflowRun)
                .values(
                    id=run_id,
                    agent_id=agent_id,
                    vendor_id=None,
                    requested_by_id=requested_by_id,
                    workflow_type="managed_agent",
                    temporal_workflow_id=temporal_workflow_id,
                    idempotency_key=idempotency_key,
                    request_fingerprint=fingerprint,
                    status=RunStatus.PENDING,
                    input_summary={"task": task},
                    version=1,
                )
                .on_conflict_do_nothing(constraint="uq_workflow_run_principal_idempotency_key")
                .returning(WorkflowRun.id)
            )
            inserted_run_id = (await session.execute(statement)).scalar_one_or_none()
            if inserted_run_id is not None:
                session.add(
                    WorkflowStartAttempt(
                        workflow_run_id=run_id,
                        status=WorkflowStartStatus.PENDING,
                    )
                )
                grants = (
                    await session.scalars(
                        select(AgentToolGrant).where(AgentToolGrant.agent_id == agent_id)
                    )
                ).all()
                for grant in grants:
                    session.add(
                        RunToolGrantSnapshot(
                            workflow_run_id=run_id,
                            tool_definition_id=grant.tool_definition_id,
                            policy_version=grant.policy_version,
                            policy_hash=grant.policy_hash,
                        )
                    )
                await AuditEventRepository(session).append(
                    AuditEvent(
                        workflow_run_id=run_id,
                        actor_id=requested_by_id,
                        action="api.managed_agent.start.request",
                        resource_type="workflow_run",
                        resource_id=run_id,
                        idempotency_key=f"{run_id}:api.managed-agent.start.request",
                        summary="Authenticated principal requested a managed agent run.",
                        evidence={"agent_id": str(agent_id)},
                    )
                )
                run = await session.get(WorkflowRun, run_id)
                assert run is not None
                return run

            run = await session.scalar(
                select(WorkflowRun).where(
                    WorkflowRun.requested_by_id == requested_by_id,
                    WorkflowRun.idempotency_key == idempotency_key,
                )
            )
            if run is None:
                raise RuntimeError("workflow idempotency conflict could not be reloaded")
            if run.request_fingerprint != fingerprint:
                raise RequestFingerprintConflict
            return run

    async def start(self, run_id: uuid.UUID) -> WorkflowRun:
        claim = await self._claim_start(run_id)
        if not claim.should_start:
            return claim.run
        assert claim.token is not None
        try:
            with trace.get_tracer(__name__).start_as_current_span("agents.workflow.start") as span:
                span.set_attribute("temporal.workflow.type", claim.run.workflow_type)
                if claim.run.workflow_type == "vendor_onboarding":
                    workflow_method = VendorOnboardingWorkflow.run
                    workflow_input: VendorOnboardingInput | ManagedAgentInput = (
                        VendorOnboardingInput(
                            run_id=str(claim.run.id), vendor_id=str(claim.run.vendor_id)
                        )
                    )
                elif claim.run.workflow_type == "managed_agent":
                    workflow_method = ManagedAgentWorkflow.run
                    workflow_input = ManagedAgentInput(run_id=str(claim.run.id))
                else:
                    raise ValueError("workflow run has an unsupported workflow type")
                await self._temporal_client.start_workflow(
                    workflow_method,
                    workflow_input,
                    id=claim.run.temporal_workflow_id,
                    task_queue=TASK_QUEUE,
                )
        except WorkflowAlreadyStartedError:
            await self._mark_started(run_id, claim.token)
            RUN_STARTS.labels("already_started").inc()
        except Exception as error:
            category = classify_start_error(error)
            await self._mark_failed(run_id, claim.token, category)
            RUN_STARTS.labels(category).inc()
            raise WorkflowStartUnavailable(category) from error
        else:
            await self._mark_started(run_id, claim.token)
            RUN_STARTS.labels("started").inc()
        return claim.run

    async def recover(self, *, limit: int = 100) -> RecoveryResult:
        """Reconcile failed or unclaimed starts without creating new workflow intent."""
        async with self._database.session() as session:
            run_ids = (
                await session.scalars(
                    select(WorkflowStartAttempt.workflow_run_id)
                    .where(WorkflowStartAttempt.status != WorkflowStartStatus.STARTED)
                    .order_by(WorkflowStartAttempt.created_at)
                    .limit(limit)
                )
            ).all()
        unavailable = 0
        for run_id in run_ids:
            try:
                await self.start(run_id)
            except WorkflowStartUnavailable:
                unavailable += 1
        return RecoveryResult(inspected=len(run_ids), unavailable=unavailable)

    async def _claim_start(self, run_id: uuid.UUID) -> StartClaim:
        now = self._now()
        async with self._database.session() as session:
            attempt = await session.scalar(
                select(WorkflowStartAttempt)
                .where(WorkflowStartAttempt.workflow_run_id == run_id)
                .with_for_update()
            )
            run = await session.get(WorkflowRun, run_id)
            if run is None or attempt is None:
                raise LookupError("workflow start intent not found")
            if attempt.status is WorkflowStartStatus.STARTED:
                return StartClaim(run=run, token=None)
            if attempt.lease_expires_at is not None and attempt.lease_expires_at > now:
                return StartClaim(run=run, token=None)
            token = uuid.uuid4()
            attempt.status = WorkflowStartStatus.PENDING
            attempt.attempt_token = token
            attempt.attempts = (attempt.attempts or 0) + 1
            attempt.error_category = None
            attempt.last_attempted_at = now
            attempt.lease_expires_at = now + self._lease_duration
            return StartClaim(run=run, token=token)

    async def _mark_started(self, run_id: uuid.UUID, token: uuid.UUID) -> None:
        async with self._database.session() as session:
            attempt = await session.scalar(
                select(WorkflowStartAttempt)
                .where(WorkflowStartAttempt.workflow_run_id == run_id)
                .with_for_update()
            )
            if attempt is not None and attempt.attempt_token == token:
                attempt.status = WorkflowStartStatus.STARTED
                attempt.error_category = None
                attempt.lease_expires_at = None
                attempt.started_at = self._now()

    async def _mark_failed(self, run_id: uuid.UUID, token: uuid.UUID, category: str) -> None:
        async with self._database.session() as session:
            attempt = await session.scalar(
                select(WorkflowStartAttempt)
                .where(WorkflowStartAttempt.workflow_run_id == run_id)
                .with_for_update()
            )
            if attempt is not None and attempt.attempt_token == token:
                attempt.status = WorkflowStartStatus.FAILED
                attempt.error_category = category
                attempt.lease_expires_at = None
