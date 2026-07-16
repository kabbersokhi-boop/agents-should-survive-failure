"""Reviewed evaluation catalog validation and production workflow execution."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Protocol, TypedDict, cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from agents_should_survive_failure.auth import (
    ApprovalAuthorizationDenied,
    AuthenticatedPrincipal,
    assert_approval_decision_authorized,
)
from agents_should_survive_failure.evaluation_scenarios import (
    ApprovalDecision,
    CancellationPoint,
    EvaluationCaseDefinition,
    EvaluationSuiteDefinition,
    FaultCategory,
    FaultPlan,
    ModelProviderMode,
    load_packaged_evaluation_suite,
)
from agents_should_survive_failure.fault_injection import FaultAction, FaultInjector, FaultPoint
from agents_should_survive_failure.mcp_adapter import GovernedMCPAdapter, MCPExecutionContext
from agents_should_survive_failure.metrics import EVALUATION_CASES
from agents_should_survive_failure.persistence.models import (
    ApprovalDecision as PersistedApprovalDecision,
)
from agents_should_survive_failure.persistence.models import (
    ApprovalRequest,
    ApprovedVendor,
    AuditEvent,
    EvaluationCase,
    EvaluationResult,
    EvaluationResultStatus,
    EvaluationRun,
    EvaluationStatus,
    FaultInjectionConsumption,
    InvocationStatus,
    ModelCall,
    PrincipalType,
    RunStatus,
    SyntheticEmailMessage,
    ToolDefinition,
    ToolInvocation,
    Vendor,
    VendorStatus,
    WorkflowEvent,
    WorkflowRun,
    WorkflowStartAttempt,
)
from agents_should_survive_failure.persistence.seed import seed_id
from agents_should_survive_failure.persistence.session import Database
from agents_should_survive_failure.tool_gateway import ToolGateway
from agents_should_survive_failure.workflow_starts import (
    WorkflowStartCoordinator,
    WorkflowStartUnavailable,
)
from agents_should_survive_failure.workflows.contracts import (
    ApprovalDecisionInput,
    ApprovalDecisionType,
)
from agents_should_survive_failure.workflows.vendor_onboarding import VendorOnboardingWorkflow


class PersistedCaseInspection(TypedDict):
    valid: bool
    expected_case_sha256: str | None
    reconstructed_case_sha256: str | None
    mismatch_reasons: list[str]


class EvaluationRequestFingerprintConflict(Exception):
    """An evaluation idempotency key was reused for a different suite request."""


class EvaluationTemporalClient(Protocol):
    """The narrow Temporal surface required by the production evaluator."""

    async def start_workflow(self, workflow: Any, arg: Any, *, id: str, task_queue: str) -> Any: ...

    def get_workflow_handle(self, workflow_id: str) -> Any: ...


_FAULT_ACTIONS: dict[FaultCategory, FaultAction] = {
    FaultCategory.RETRYABLE: FaultAction.RETRYABLE_EXCEPTION,
    FaultCategory.PERMANENT: FaultAction.PERMANENT_EXCEPTION,
    FaultCategory.PROCESS_TERMINATION: FaultAction.WORKER_TERMINATION,
    FaultCategory.AMBIGUOUS_HANDOFF: FaultAction.AMBIGUOUS_HANDOFF,
}


def evaluation_request_fingerprint(suite: EvaluationSuiteDefinition) -> str:
    """Hash the complete logical B1 request independently of caller and replay key."""

    payload = {
        "dataset_sha256": suite.content_sha256(),
        "suite_schema_version": suite.schema_version,
        "suite_slug": suite.suite_slug,
        "suite_version": suite.suite_version,
        "workflow_type": suite.workflow_type,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


class EvaluationRunner:
    """Run the explicitly limited B1 catalog-persistence integrity checks.

    This runner does not execute the production workflow and does not score business behavior.
    Phase B2 will replace it with real Temporal execution while preserving the reviewed suite
    contracts and immutable result snapshots introduced in B1.
    """

    async def run_vendor_onboarding(
        self, session: AsyncSession, *, requested_by_id: UUID, idempotency_key: str
    ) -> EvaluationRun:
        suite = load_packaged_evaluation_suite()
        dataset_sha256 = suite.content_sha256()
        request_fingerprint = evaluation_request_fingerprint(suite)
        definitions = {case.slug: case for case in suite.cases}
        now = datetime.now(UTC)
        run_id = uuid.uuid4()
        inserted = await session.execute(
            insert(EvaluationRun)
            .values(
                id=run_id,
                requested_by_id=requested_by_id,
                idempotency_key=idempotency_key,
                request_fingerprint=request_fingerprint,
                suite_slug=suite.suite_slug,
                suite_version=suite.suite_version,
                suite_schema_version=suite.schema_version,
                dataset_sha256=dataset_sha256,
                status=EvaluationStatus.RUNNING,
                configuration={
                    "workflow_type": suite.workflow_type,
                    "execution_mode": "b1_catalog_persistence_integrity",
                    "workflow_executed": False,
                    "dataset_case_count": len(suite.cases),
                    "catalog_case_count_expected": len(suite.cases),
                },
                started_at=now,
            )
            .on_conflict_do_nothing(constraint="uq_evaluation_run_principal_idempotency_key")
            .returning(EvaluationRun.id)
        )
        inserted_run_id = inserted.scalar_one_or_none()
        if inserted_run_id is None:
            existing = await session.scalar(
                select(EvaluationRun).where(
                    EvaluationRun.requested_by_id == requested_by_id,
                    EvaluationRun.idempotency_key == idempotency_key,
                )
            )
            if existing is None:
                raise RuntimeError("evaluation idempotency conflict could not be reloaded")
            if existing.request_fingerprint != request_fingerprint:
                raise EvaluationRequestFingerprintConflict
            return existing
        run = await session.get(EvaluationRun, run_id)
        if run is None:
            raise RuntimeError("inserted evaluation run could not be reloaded")

        # A schema-version mismatch is catalog drift. Do not filter it away before inspection.
        result = await session.scalars(
            select(EvaluationCase)
            .where(
                EvaluationCase.suite_slug == suite.suite_slug,
                EvaluationCase.suite_version == suite.suite_version,
            )
            .order_by(EvaluationCase.slug)
        )
        cases = result.all()
        persisted_slugs = {case.slug for case in cases}
        expected_slugs = set(definitions)
        missing_slugs = sorted(expected_slugs - persisted_slugs)
        unexpected_slugs = sorted(persisted_slugs - expected_slugs)
        unexpected_enabled_slugs = sorted(
            case.slug for case in cases if case.slug not in definitions and case.enabled
        )
        unexpected_disabled_slugs = sorted(
            case.slug for case in cases if case.slug not in definitions and not case.enabled
        )
        disabled_expected_slugs = sorted(
            case.slug for case in cases if case.slug in definitions and not case.enabled
        )
        run.configuration = {
            **run.configuration,
            "catalog_case_count_found": len(cases),
            "missing_case_slugs": missing_slugs,
            "unexpected_case_slugs": unexpected_slugs,
            "unexpected_enabled_case_slugs": unexpected_enabled_slugs,
            "unexpected_disabled_case_slugs": unexpected_disabled_slugs,
            "disabled_expected_case_slugs": disabled_expected_slugs,
        }

        all_cases_passed = not (
            missing_slugs
            or unexpected_enabled_slugs
            or unexpected_disabled_slugs
            or disabled_expected_slugs
        )
        if missing_slugs:
            EVALUATION_CASES.labels("failed").inc(len(missing_slugs))

        for case in cases:
            definition = definitions.get(case.slug)
            inspection = inspect_persisted_case(suite, definition, case)
            if definition is not None and not case.enabled:
                inspection["mismatch_reasons"].append("expected_case_disabled")
            passed = inspection["valid"] is True
            all_cases_passed = all_cases_passed and passed
            EVALUATION_CASES.labels("passed" if passed else "failed").inc()

            expected_outcome = (
                definition.expected_outcome.model_dump(mode="json")
                if definition is not None
                else case.expected_outcome
            )
            actual_outcome = {
                "catalog_record_valid": passed,
                "workflow_executed": False,
                "execution_mode": "b1_catalog_persistence_integrity",
                "mismatch_reasons": inspection["mismatch_reasons"],
            }
            session.add(
                EvaluationResult(
                    evaluation_run_id=run.id,
                    evaluation_case_id=case.id,
                    case_slug=case.slug,
                    case_version=case.version,
                    case_content_sha256=case.content_sha256,
                    status=(
                        EvaluationResultStatus.PASSED if passed else EvaluationResultStatus.FAILED
                    ),
                    score=Decimal("1.0000") if passed else Decimal("0.0000"),
                    expected_outcome=expected_outcome,
                    actual_outcome=actual_outcome,
                    failure_category=None if passed else "catalog_persistence_mismatch",
                    duration_ms=0,
                    metrics={
                        "catalog_record_valid": passed,
                        "workflow_executed": False,
                    },
                    evidence_summary={
                        "dataset_sha256": dataset_sha256,
                        "expected_case_sha256": inspection["expected_case_sha256"],
                        "persisted_case_sha256": case.content_sha256,
                        "reconstructed_case_sha256": inspection["reconstructed_case_sha256"],
                        "mismatch_reasons": inspection["mismatch_reasons"],
                        "note": (
                            "B1 validates catalog persistence only; no Temporal workflow ran."
                        ),
                    },
                    summary=(
                        "B1 catalog persistence matched; no Temporal workflow was executed."
                        if passed
                        else (
                            "B1 catalog persistence did not match the packaged contract; "
                            "no Temporal workflow was executed."
                        )
                    ),
                )
            )

        run.status = EvaluationStatus.SUCCEEDED if all_cases_passed else EvaluationStatus.FAILED
        run.completed_at = datetime.now(UTC)
        return run

    async def run_production_vendor_onboarding(
        self,
        database: Database,
        temporal_client: EvaluationTemporalClient,
        *,
        requested_by_id: UUID,
        idempotency_key: str,
        fault_injection_enabled: bool,
        timeout_seconds: float = 90.0,
    ) -> EvaluationRun:
        """Execute every reviewed case against the real vendor Temporal workflow.

        The catalog-only method above remains a narrow migration validator for legacy callers.
        API and release gates use this method: it creates an isolated vendor and persisted start
        intent for each case, lets the installed worker execute production activities, then scores
        evidence already owned by PostgreSQL.  It deliberately has no independent jurisdiction
        rule or synthetic outcome implementation.
        """

        suite = load_packaged_evaluation_suite()
        request_fingerprint = evaluation_request_fingerprint(suite)
        async with database.session() as session:
            run = await self._create_execution_run(
                session,
                suite=suite,
                requested_by_id=requested_by_id,
                idempotency_key=idempotency_key,
                request_fingerprint=request_fingerprint,
            )
            if run is None:
                existing = await session.scalar(
                    select(EvaluationRun).where(
                        EvaluationRun.requested_by_id == requested_by_id,
                        EvaluationRun.idempotency_key == idempotency_key,
                    )
                )
                if existing is None:
                    raise RuntimeError("evaluation idempotency conflict could not be reloaded")
                if existing.request_fingerprint != request_fingerprint:
                    raise EvaluationRequestFingerprintConflict
                return existing
            persisted_cases = {
                item.slug: item
                for item in (
                    await session.scalars(
                        select(EvaluationCase).where(
                            EvaluationCase.suite_slug == suite.suite_slug,
                            EvaluationCase.suite_version == suite.suite_version,
                        )
                    )
                ).all()
            }

        injector = FaultInjector(database, enabled=fault_injection_enabled)
        all_passed = True
        for definition in suite.cases:
            persisted_case = persisted_cases.get(definition.slug)
            if persisted_case is None:
                await self._record_invalid_case(
                    database, run.id, definition, "missing_persisted_case"
                )
                all_passed = False
                continue
            try:
                passed = await self._execute_case(
                    database,
                    temporal_client,
                    injector,
                    evaluation_run_id=run.id,
                    evaluation_case=persisted_case,
                    definition=definition,
                    requested_by_id=requested_by_id,
                    timeout_seconds=timeout_seconds,
                )
            except Exception as error:
                await self._record_case_error(
                    database,
                    run.id,
                    persisted_case,
                    definition,
                    error,
                )
                passed = False
            all_passed = all_passed and passed

        async with database.session() as session:
            completed = await session.get(EvaluationRun, run.id)
            if completed is None:
                raise RuntimeError("evaluation run disappeared")
            completed.status = EvaluationStatus.SUCCEEDED if all_passed else EvaluationStatus.FAILED
            completed.completed_at = datetime.now(UTC)
            completed.configuration = {
                **completed.configuration,
                "workflow_executed": True,
                "execution_mode": "production_temporal_workflow",
            }
            return completed

    @staticmethod
    async def _create_execution_run(
        session: AsyncSession,
        *,
        suite: EvaluationSuiteDefinition,
        requested_by_id: UUID,
        idempotency_key: str,
        request_fingerprint: str,
    ) -> EvaluationRun | None:
        run_id = uuid.uuid4()
        inserted = await session.execute(
            insert(EvaluationRun)
            .values(
                id=run_id,
                requested_by_id=requested_by_id,
                idempotency_key=idempotency_key,
                request_fingerprint=request_fingerprint,
                suite_slug=suite.suite_slug,
                suite_version=suite.suite_version,
                suite_schema_version=suite.schema_version,
                dataset_sha256=suite.content_sha256(),
                status=EvaluationStatus.RUNNING,
                configuration={
                    "workflow_type": suite.workflow_type,
                    "execution_mode": "production_temporal_workflow",
                    "workflow_executed": True,
                    "dataset_case_count": len(suite.cases),
                },
                started_at=datetime.now(UTC),
            )
            .on_conflict_do_nothing(constraint="uq_evaluation_run_principal_idempotency_key")
            .returning(EvaluationRun.id)
        )
        if inserted.scalar_one_or_none() is None:
            return None
        return await session.get(EvaluationRun, run_id)

    async def _execute_case(
        self,
        database: Database,
        temporal_client: EvaluationTemporalClient,
        injector: FaultInjector,
        *,
        evaluation_run_id: UUID,
        evaluation_case: EvaluationCase,
        definition: EvaluationCaseDefinition,
        requested_by_id: UUID,
        timeout_seconds: float,
    ) -> bool:
        started = time.monotonic()
        token = uuid.uuid4().hex
        vendor_id = await self._create_isolated_vendor(database, definition, token)
        allowed_ids = await self._allowed_tool_ids(database, definition)
        coordinator = WorkflowStartCoordinator(database, temporal_client, fault_injector=injector)
        workflow_run = await coordinator.create_or_get(
            vendor_id=vendor_id,
            requested_by_id=requested_by_id,
            agent_id=seed_id("agent:vendor-onboarding:v1"),
            idempotency_key=f"evaluation:{evaluation_run_id}:{definition.slug}:{token}",
            allowed_tool_definition_ids=allowed_ids,
        )
        await self._configure_faults(injector, workflow_run.id, definition)
        try:
            await coordinator.start(workflow_run.id)
        except WorkflowStartUnavailable:
            # The intent is already durable. Reconciliation must converge on the same workflow ID.
            await coordinator.recover(limit=1)

        await self._drive_case(
            database,
            temporal_client,
            workflow_run,
            definition,
            requested_by_id,
            timeout_seconds,
        )
        actual = await self._collect_actual(database, workflow_run.id)
        passed, mismatches = self._score_case(definition, actual)
        EVALUATION_CASES.labels("passed" if passed else "failed").inc()
        await self._persist_case_result(
            database,
            evaluation_run_id=evaluation_run_id,
            evaluation_case=evaluation_case,
            definition=definition,
            workflow_run_id=workflow_run.id,
            actual=actual,
            passed=passed,
            mismatches=mismatches,
            duration_ms=int((time.monotonic() - started) * 1000),
        )
        return passed

    @staticmethod
    async def _create_isolated_vendor(
        database: Database, definition: EvaluationCaseDefinition, token: str
    ) -> UUID:
        async with database.session() as session:
            vendor = Vendor(
                external_reference=f"eval-{definition.input.external_reference_prefix}-{token[:12]}",
                legal_name=definition.input.legal_name,
                jurisdiction=definition.input.jurisdiction,
                contact_email=(
                    f"eval-{token[:12]}-{definition.input.contact_email.split('@', 1)[0]}"
                    "@example.invalid"
                ),
                status=VendorStatus.SUBMITTED,
            )
            session.add(vendor)
            await session.flush()
            return vendor.id

    @staticmethod
    async def _allowed_tool_ids(
        database: Database, definition: EvaluationCaseDefinition
    ) -> set[UUID]:
        async with database.session() as session:
            tools = (await session.scalars(select(ToolDefinition))).all()
        omitted = {item.value for item in definition.setup.omitted_tool_grants}
        return {tool.id for tool in tools if tool.name not in omitted}

    @staticmethod
    async def _configure_faults(
        injector: FaultInjector, workflow_run_id: UUID, definition: EvaluationCaseDefinition
    ) -> None:
        faults = list(definition.setup.faults)
        if definition.setup.model_provider_mode is ModelProviderMode.FAIL_EXPLANATION:
            faults.append(
                FaultPlan(
                    fault_point=FaultPoint.PROVIDER_CALL.value,
                    category=FaultCategory.RETRYABLE,
                    trigger_count=1,
                    retryable=True,
                )
            )
        if not faults:
            return
        for fault in faults:
            await injector.create(
                fault_point=FaultPoint(fault.fault_point),
                action=_FAULT_ACTIONS[fault.category],
                scope_key=str(workflow_run_id),
                trigger_count=fault.trigger_count,
                delay_ms=fault.delay_ms,
                safe_metadata={"evaluation": definition.slug},
            )

    async def _drive_case(
        self,
        database: Database,
        temporal_client: EvaluationTemporalClient,
        workflow_run: WorkflowRun,
        definition: EvaluationCaseDefinition,
        requested_by_id: UUID,
        timeout_seconds: float,
    ) -> None:
        if definition.expected_outcome.run_status == "failed":
            await self._wait_for_status(
                database, workflow_run.id, {RunStatus.FAILED}, timeout_seconds
            )
            return
        handle = temporal_client.get_workflow_handle(workflow_run.temporal_workflow_id)
        await self._drive_early_approval_attempts(handle, workflow_run, definition, requested_by_id)
        await self._drive_tool_attempts(database, workflow_run, definition)
        approval = await self._wait_for_approval(database, workflow_run.id, timeout_seconds)
        await self._wait_for_approval_phase(handle, timeout_seconds)
        if definition.driver.cancellation_point is not CancellationPoint.NONE:
            await handle.signal(VendorOnboardingWorkflow.cancel)
            await self._wait_for_status(
                database, workflow_run.id, {RunStatus.CANCELLED}, timeout_seconds
            )
            return

        accepted_key: str | None = None
        for index, attempt in enumerate(definition.driver.approval_attempts):
            if attempt.timing.value == "before_request":
                continue
            if attempt.actor.value == "unauthorized":
                unauthorized_principal = AuthenticatedPrincipal(
                    id=requested_by_id,
                    key_id=uuid.uuid4(),
                    scopes=frozenset({"runs:read"}),
                    principal_type=PrincipalType.USER,
                )
                try:
                    assert_approval_decision_authorized(unauthorized_principal)
                except ApprovalAuthorizationDenied:
                    continue
                raise RuntimeError("unauthorized approval probe was unexpectedly authorized")
            key = (
                accepted_key
                if attempt.idempotency_key_mode.value == "reuse_previous"
                else (f"evaluation:{workflow_run.id}:decision:{index}")
            )
            if key is None:
                raise RuntimeError("evaluation replay lacks an accepted idempotency key")
            expected_version = approval.version if attempt.version_mode.value == "current" else 0
            decision = (
                ApprovalDecisionType.APPROVED
                if attempt.decision is ApprovalDecision.APPROVED
                else ApprovalDecisionType.REJECTED
            )
            payload = ApprovalDecisionInput(
                approval_request_id=str(approval.id),
                expected_version=expected_version,
                decision=decision,
                decided_by_id=str(requested_by_id),
                rationale="Reviewed evaluation decision.",
                idempotency_key=key,
            )
            try:
                await self._execute_accepted_update(
                    handle,
                    payload,
                    timeout_seconds=timeout_seconds,
                    required=attempt.expected_effect.value == "accepted",
                )
            except Exception:
                # Rejected probes are evidence-bearing only when the production API/service
                # persists them. The accepted reviewed action below still drives the workflow.
                if attempt.expected_effect.value == "accepted":
                    raise
            else:
                if attempt.expected_effect.value == "accepted":
                    accepted_key = key
        expected_status = (
            RunStatus.SUCCEEDED
            if definition.expected_outcome.run_status == "succeeded"
            else RunStatus.REJECTED
        )
        await self._wait_for_status(database, workflow_run.id, {expected_status}, timeout_seconds)

    @staticmethod
    async def _execute_accepted_update(
        handle: Any,
        payload: ApprovalDecisionInput,
        *,
        timeout_seconds: float,
        required: bool,
    ) -> None:
        """Retry a reviewed accepted update while a retried wait activity becomes visible.

        The same workflow-update ID and approval idempotency key make this safe if a successful
        update acknowledgement is lost. Rejected probes stay single-shot so their evidence is not
        distorted by evaluator retries.
        """

        if not required:
            await handle.execute_update("decide", payload, id=payload.idempotency_key)
            return
        deadline = time.monotonic() + min(timeout_seconds, 10.0)
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            try:
                await handle.execute_update("decide", payload, id=payload.idempotency_key)
                return
            except Exception as error:
                last_error = error
                await _sleep_poll()
        if last_error is not None:
            raise last_error
        raise TimeoutError("approval update did not complete")

    @staticmethod
    async def _drive_early_approval_attempts(
        handle: Any,
        workflow_run: WorkflowRun,
        definition: EvaluationCaseDefinition,
        requested_by_id: UUID,
    ) -> None:
        """Send reviewed early-decision probes before a request can be accepted."""

        for index, attempt in enumerate(definition.driver.approval_attempts):
            if attempt.timing.value != "before_request":
                continue
            decision = (
                ApprovalDecisionType.APPROVED
                if attempt.decision is ApprovalDecision.APPROVED
                else ApprovalDecisionType.REJECTED
            )
            payload = ApprovalDecisionInput(
                approval_request_id=str(uuid.uuid4()),
                expected_version=1,
                decision=decision,
                decided_by_id=str(requested_by_id),
                rationale="Reviewed early evaluation probe.",
                idempotency_key=f"evaluation:{workflow_run.id}:early:{index}",
            )
            try:
                await handle.execute_update("decide", payload, id=payload.idempotency_key)
            except Exception:
                continue
            raise RuntimeError("early approval probe was unexpectedly accepted")

    @staticmethod
    async def _drive_tool_attempts(
        database: Database,
        workflow_run: WorkflowRun,
        definition: EvaluationCaseDefinition,
    ) -> None:
        """Exercise reviewed probes through the production MCP adapter and governed gateway."""

        adapter = GovernedMCPAdapter(ToolGateway(database))
        mcp_names = {
            "vendor_database_query": "vendor.lookup",
            "internal_policy_search": "policy.search",
            "synthetic_email_send": "email.send",
        }
        for index, attempt in enumerate(definition.driver.tool_attempts):
            if attempt.timing.value != "after_run_created":
                continue
            agent_id = (
                str(workflow_run.agent_id)
                if attempt.actor.value == "managed_agent"
                else str(uuid.uuid4())
            )
            try:
                async with database.session() as session:
                    await adapter.call(
                        session,
                        context=MCPExecutionContext(
                            workflow_run_id=str(workflow_run.id),
                            agent_id=agent_id,
                            correlation_id=f"{workflow_run.id}:evaluation-probe:{index}",
                        ),
                        tool_name=mcp_names[attempt.tool_name.value],
                        arguments=dict(attempt.arguments),
                        idempotency_key=f"evaluation:{workflow_run.id}:tool-probe:{index}",
                    )
            except Exception:
                continue
            raise RuntimeError("reviewed tool probe was unexpectedly accepted")

    @staticmethod
    async def _wait_for_approval_phase(handle: Any, timeout_seconds: float) -> None:
        """Wait until the workflow has durably transitioned to its update-accepting phase."""

        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            try:
                status = await handle.query("status")
            except Exception:
                await _sleep_poll()
                continue
            phase: object | None
            if isinstance(status, dict):
                phase = cast(dict[str, object], status).get("phase")
            else:
                phase = getattr(status, "phase", None)
            if phase == "waiting_for_approval":
                return
            await _sleep_poll()
        raise TimeoutError("evaluation workflow did not enter the approval wait phase")

    @staticmethod
    async def _wait_for_approval(
        database: Database, workflow_run_id: UUID, timeout_seconds: float
    ) -> ApprovalRequest:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            async with database.session() as session:
                approval = await session.scalar(
                    select(ApprovalRequest).where(
                        ApprovalRequest.workflow_run_id == workflow_run_id
                    )
                )
            if approval is not None:
                return approval
            await _sleep_poll()
        raise TimeoutError("evaluation workflow did not create an approval request")

    @staticmethod
    async def _wait_for_status(
        database: Database,
        workflow_run_id: UUID,
        statuses: set[RunStatus],
        timeout_seconds: float,
    ) -> None:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            async with database.session() as session:
                run = await session.get(WorkflowRun, workflow_run_id)
            if run is not None and run.status in statuses:
                return
            await _sleep_poll()
        expected = ", ".join(status.value for status in statuses)
        raise TimeoutError(f"evaluation workflow did not reach {expected}")

    @staticmethod
    async def _collect_actual(database: Database, workflow_run_id: UUID) -> dict[str, Any]:
        async with database.session() as session:
            run = await session.get(WorkflowRun, workflow_run_id)
            if run is None or run.vendor_id is None:
                raise RuntimeError("evaluation workflow run is missing")
            vendor = await session.get(Vendor, run.vendor_id)
            approvals = (
                await session.scalars(
                    select(ApprovalRequest).where(
                        ApprovalRequest.workflow_run_id == workflow_run_id
                    )
                )
            ).all()
            approval_ids = [approval.id for approval in approvals]
            decisions: list[PersistedApprovalDecision] = []
            if approval_ids:
                decisions = list(
                    (
                        await session.scalars(
                            select(PersistedApprovalDecision).where(
                                PersistedApprovalDecision.approval_request_id.in_(approval_ids)
                            )
                        )
                    ).all()
                )
            events = (
                await session.scalars(
                    select(WorkflowEvent)
                    .where(WorkflowEvent.workflow_run_id == workflow_run_id)
                    .order_by(WorkflowEvent.sequence)
                )
            ).all()
            invocations = (
                await session.scalars(
                    select(ToolInvocation).where(ToolInvocation.workflow_run_id == workflow_run_id)
                )
            ).all()
            model_calls = (
                await session.scalars(
                    select(ModelCall).where(ModelCall.workflow_run_id == workflow_run_id)
                )
            ).all()
            projections = (
                await session.scalars(
                    select(ApprovedVendor).where(ApprovedVendor.workflow_run_id == workflow_run_id)
                )
            ).all()
            emails = (
                await session.scalars(
                    select(SyntheticEmailMessage).where(
                        SyntheticEmailMessage.workflow_run_id == workflow_run_id
                    )
                )
            ).all()
            audit_events = (
                await session.scalars(
                    select(AuditEvent).where(AuditEvent.workflow_run_id == workflow_run_id)
                )
            ).all()
            start_attempt = await session.scalar(
                select(WorkflowStartAttempt).where(
                    WorkflowStartAttempt.workflow_run_id == workflow_run_id
                )
            )
            fault_consumptions = (
                await session.scalars(
                    select(FaultInjectionConsumption).where(
                        FaultInjectionConsumption.scope_key == str(workflow_run_id)
                    )
                )
            ).all()
        approval_status = approvals[0].status.value if approvals else "absent"
        failure_category = None
        for event in events:
            if event.event_type == "review.failed":
                failure_category = str(event.payload.get("category"))
        by_tool = {
            name: [
                {
                    "status": invocation.status.value,
                    "error_category": invocation.error_category,
                }
                for invocation in invocations
                if invocation.requested_tool_name == name
            ]
            for name in {invocation.requested_tool_name for invocation in invocations}
        }
        return {
            "run_status": run.status.value,
            "vendor_status": vendor.status.value if vendor is not None else "missing",
            "risk_score": vendor.risk_score if vendor is not None else None,
            "approval_request_count": len(approvals),
            "approval_decision_count": len(decisions),
            "approval_status": approval_status,
            "approved_vendor_count": len(projections),
            "synthetic_email_count": len(emails),
            "model_call_status": (
                "absent"
                if not model_calls
                else "failed"
                if any(call.status is InvocationStatus.FAILED for call in model_calls)
                else "succeeded"
            ),
            "failure_category": failure_category,
            "workflow_event_types": [event.event_type for event in events],
            "workflow_event_sequences": [event.sequence for event in events],
            "tool_invocations": by_tool,
            "workflow_start_attempts": start_attempt.attempts if start_attempt is not None else 0,
            "activity_retry_count": len(fault_consumptions),
            "audit_event_count": len(audit_events),
            "fault_consumption_count": len(fault_consumptions),
            "model_metadata": [
                {"provider": call.provider, "model": call.model, "status": call.status.value}
                for call in model_calls
            ],
        }

    @staticmethod
    def _score_case(
        definition: EvaluationCaseDefinition, actual: dict[str, Any]
    ) -> tuple[bool, list[str]]:
        expected = definition.expected_outcome
        mismatches: list[str] = []
        scalar_names = (
            "run_status",
            "vendor_status",
            "risk_score",
            "approval_request_count",
            "approval_decision_count",
            "approval_status",
            "approved_vendor_count",
            "synthetic_email_count",
        )
        for name in scalar_names:
            if actual[name] != getattr(expected, name):
                mismatches.append(
                    f"{name}: expected {getattr(expected, name)!r}, got {actual[name]!r}"
                )
        if (
            expected.model_call_status != "any"
            and actual["model_call_status"] != expected.model_call_status
        ):
            mismatches.append("model_call_status")
        if actual["failure_category"] != expected.failure_category:
            mismatches.append("failure_category")
        if int(actual["workflow_start_attempts"]) < expected.workflow_start_attempt_count_min:
            mismatches.append("workflow_start_attempt_count")
        if int(actual["activity_retry_count"]) < expected.activity_retry_count_min:
            mismatches.append("activity_retry_count")
        actual_events = actual["workflow_event_types"]
        if actual_events != [event.value for event in expected.expected_event_types]:
            mismatches.append("workflow_event_types")
        sequences = actual["workflow_event_sequences"]
        if expected.duplicate_prevention.workflow_event_sequences_unique and len(sequences) != len(
            set(sequences)
        ):
            mismatches.append("workflow_event_sequences_not_unique")
        if actual["approval_decision_count"] > expected.duplicate_prevention.approval_decisions_max:
            mismatches.append("duplicate_approval_decision")
        if actual["approved_vendor_count"] > expected.duplicate_prevention.approved_vendor_rows_max:
            mismatches.append("duplicate_approved_vendor")
        if actual["synthetic_email_count"] > expected.duplicate_prevention.synthetic_email_rows_max:
            mismatches.append("duplicate_synthetic_email")
        invocations = cast(dict[str, list[dict[str, Any]]], actual["tool_invocations"])
        for expectation in expected.tool_invocations:
            records = invocations.get(expectation.tool_name.value, [])
            if not (expectation.minimum_count <= len(records) <= expectation.maximum_count):
                mismatches.append(f"tool_count:{expectation.tool_name.value}")
                continue
            statuses = {record["status"] for record in records}
            errors = {record["error_category"] for record in records if record["error_category"]}
            if not set(expectation.required_statuses).issubset(statuses):
                mismatches.append(f"tool_status:{expectation.tool_name.value}")
            if not set(expectation.required_error_categories).issubset(errors):
                mismatches.append(f"tool_error:{expectation.tool_name.value}")
        return not mismatches, mismatches

    @staticmethod
    async def _persist_case_result(
        database: Database,
        *,
        evaluation_run_id: UUID,
        evaluation_case: EvaluationCase,
        definition: EvaluationCaseDefinition,
        workflow_run_id: UUID,
        actual: dict[str, Any],
        passed: bool,
        mismatches: list[str],
        duration_ms: int,
    ) -> None:
        async with database.session() as session:
            session.add(
                EvaluationResult(
                    evaluation_run_id=evaluation_run_id,
                    evaluation_case_id=evaluation_case.id,
                    case_slug=definition.slug,
                    case_version=definition.case_version,
                    case_content_sha256=evaluation_case.content_sha256,
                    workflow_run_id=workflow_run_id,
                    status=(
                        EvaluationResultStatus.PASSED if passed else EvaluationResultStatus.FAILED
                    ),
                    score=Decimal("1.0000") if passed else Decimal("0.0000"),
                    expected_outcome=definition.expected_outcome.model_dump(mode="json"),
                    actual_outcome=actual,
                    failure_category=None if passed else ";".join(mismatches)[:120],
                    duration_ms=duration_ms,
                    metrics={
                        "workflow_start_attempts": actual["workflow_start_attempts"],
                        "activity_retry_count": actual["activity_retry_count"],
                        "tool_invocation_count": sum(
                            len(value) for value in actual["tool_invocations"].values()
                        ),
                    },
                    evidence_summary={
                        "workflow_run_id": str(workflow_run_id),
                        "workflow_events": actual["workflow_event_types"],
                        "model_metadata": actual["model_metadata"],
                        "audit_event_count": actual["audit_event_count"],
                        "fault_consumption_count": actual["fault_consumption_count"],
                    },
                    summary=(
                        "Production workflow evidence matched the reviewed contract."
                        if passed
                        else "Production workflow evidence mismatched: " + ", ".join(mismatches)
                    ),
                )
            )

    @staticmethod
    async def _record_invalid_case(
        database: Database,
        evaluation_run_id: UUID,
        definition: EvaluationCaseDefinition,
        category: str,
    ) -> None:
        async with database.session() as session:
            session.add(
                EvaluationResult(
                    evaluation_run_id=evaluation_run_id,
                    evaluation_case_id=seed_id(
                        f"evaluation:vendor-onboarding-phase-b:1.0.0:{definition.slug}:"
                        f"v{definition.case_version}"
                    ),
                    case_slug=definition.slug,
                    case_version=definition.case_version,
                    case_content_sha256="0" * 64,
                    workflow_run_id=None,
                    status=EvaluationResultStatus.ERROR,
                    score=Decimal("0.0000"),
                    expected_outcome=definition.expected_outcome.model_dump(mode="json"),
                    actual_outcome={},
                    failure_category=category,
                    duration_ms=0,
                    metrics={},
                    evidence_summary={},
                    summary=(
                        "Case could not execute because its persisted reviewed contract is invalid."
                    ),
                )
            )

    @staticmethod
    async def _record_case_error(
        database: Database,
        evaluation_run_id: UUID,
        evaluation_case: EvaluationCase,
        definition: EvaluationCaseDefinition,
        error: Exception,
    ) -> None:
        async with database.session() as session:
            session.add(
                EvaluationResult(
                    evaluation_run_id=evaluation_run_id,
                    evaluation_case_id=evaluation_case.id,
                    case_slug=definition.slug,
                    case_version=definition.case_version,
                    case_content_sha256=evaluation_case.content_sha256,
                    workflow_run_id=None,
                    status=EvaluationResultStatus.ERROR,
                    score=Decimal("0.0000"),
                    expected_outcome=definition.expected_outcome.model_dump(mode="json"),
                    actual_outcome={},
                    failure_category=type(error).__name__.lower()[:120],
                    duration_ms=None,
                    metrics={},
                    evidence_summary={"error_type": type(error).__name__},
                    summary="Production workflow execution could not complete.",
                )
            )


async def _sleep_poll() -> None:
    await asyncio.sleep(0.2)


def inspect_persisted_case(
    suite: EvaluationSuiteDefinition,
    definition: EvaluationCaseDefinition | None,
    case: EvaluationCase,
) -> PersistedCaseInspection:
    """Compare a persisted row with the packaged contract without scoring business behavior."""

    mismatch_reasons: list[str] = []
    expected_hash = suite.case_content_sha256(definition) if definition is not None else None
    reconstructed_hash: str | None = None
    persisted_definition: EvaluationCaseDefinition | None = None

    try:
        persisted_definition = EvaluationCaseDefinition.model_validate(
            {
                "slug": case.slug,
                "case_version": case.version,
                "scenario_type": case.scenario_type,
                "title": case.title,
                "description": case.description,
                "input": case.input_data,
                "setup": case.setup,
                "driver": case.driver,
                "expected_outcome": case.expected_outcome,
                "evidence_requirements": case.evidence_requirements,
            }
        )
        reconstructed_hash = suite.case_content_sha256(persisted_definition)
    except (TypeError, ValueError):
        mismatch_reasons.append("persisted_contract_invalid")

    if definition is None:
        mismatch_reasons.append("unexpected_case_slug")
    elif persisted_definition != definition:
        mismatch_reasons.append("persisted_contract_differs")

    metadata_checks = {
        "suite_slug_mismatch": case.suite_slug != suite.suite_slug,
        "suite_version_mismatch": case.suite_version != suite.suite_version,
        "schema_version_mismatch": case.schema_version != suite.schema_version,
        "workflow_type_mismatch": case.workflow_type != suite.workflow_type,
        "reviewed_by_mismatch": case.reviewed_by != suite.reviewed_by,
        "reviewed_at_mismatch": case.reviewed_at != suite.reviewed_at,
        "stored_case_hash_mismatch": case.content_sha256 != expected_hash,
        "reconstructed_case_hash_mismatch": reconstructed_hash != expected_hash,
    }
    mismatch_reasons.extend(reason for reason, failed in metadata_checks.items() if failed)

    return {
        "valid": not mismatch_reasons,
        "expected_case_sha256": expected_hash,
        "reconstructed_case_sha256": reconstructed_hash,
        "mismatch_reasons": mismatch_reasons,
    }
