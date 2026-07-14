"""Transaction-owning activities for the vendor-onboarding workflow."""

import uuid
from dataclasses import asdict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from temporalio import activity

from agents_should_survive_failure.mcp_adapter import GovernedMCPAdapter, MCPExecutionContext
from agents_should_survive_failure.metrics import (
    ACTIVE_RUNS,
    APPROVAL_DECISIONS,
    APPROVAL_REQUESTS,
    RUN_OUTCOMES,
)
from agents_should_survive_failure.model_evidence import ModelEvidenceService
from agents_should_survive_failure.persistence.models import (
    ApprovalDecision,
    ApprovalRequest,
    ApprovalStatus,
    ApprovedVendor,
    AuditEvent,
    RunStatus,
    VendorStatus,
    WorkflowEvent,
)
from agents_should_survive_failure.persistence.repositories import (
    AuditEventRepository,
    VendorRepository,
    WorkflowRunRepository,
)
from agents_should_survive_failure.persistence.session import Database
from agents_should_survive_failure.policy import PolicyRetriever
from agents_should_survive_failure.providers import DeterministicModelProvider, ModelProvider
from agents_should_survive_failure.workflows.contracts import (
    ApprovalDecisionInput,
    ApprovalDecisionType,
    RiskAssessment,
    VendorOnboardingInput,
)


class VendorOnboardingActivities:
    """Persist workflow effects atomically and make retries harmless."""

    def __init__(
        self,
        database: Database,
        model_provider: ModelProvider | None = None,
        policy_retriever: PolicyRetriever | None = None,
        mcp_adapter: GovernedMCPAdapter | None = None,
    ) -> None:
        self._database = database
        self._model_provider = model_provider or DeterministicModelProvider()
        self._policy_retriever = policy_retriever or PolicyRetriever()
        self._mcp_adapter = mcp_adapter

    @staticmethod
    def _id(value: str) -> uuid.UUID:
        return uuid.UUID(value)

    @activity.defn(name="vendor_onboarding.begin_review")
    async def begin_review(self, input: VendorOnboardingInput) -> None:
        run_id = self._id(input.run_id)
        vendor_id = self._id(input.vendor_id)
        async with self._database.session() as session:
            runs = WorkflowRunRepository(session)
            vendors = VendorRepository(session)
            run = await runs.get(run_id)
            vendor = await vendors.get(vendor_id, for_update=True)
            if run is None or vendor is None:
                raise ValueError("workflow run or vendor does not exist")
            if run.status is RunStatus.PENDING:
                run.status = RunStatus.RUNNING
                run.started_at = activity.info().started_time
                ACTIVE_RUNS.labels("running").inc()
            if vendor.status is VendorStatus.SUBMITTED:
                vendor.status = VendorStatus.UNDER_REVIEW
            if self._mcp_adapter is not None:
                await self._mcp_adapter.call(
                    session,
                    context=MCPExecutionContext(
                        workflow_run_id=str(run_id),
                        agent_id=str(run.agent_id),
                        correlation_id=f"{run_id}:review",
                    ),
                    tool_name="vendor.lookup",
                    arguments={"external_reference": vendor.external_reference},
                    idempotency_key=f"{run_id}:vendor-lookup",
                )
            await self._append_event(
                runs,
                run_id,
                "review.started",
                "Vendor review started.",
                {"vendor_id": input.vendor_id},
                sequence=10,
            )
            await self._audit(
                session,
                run_id,
                "vendor.review.start",
                "vendor",
                vendor_id,
                "Vendor review started.",
            )

    @activity.defn(name="vendor_onboarding.assess_risk")
    async def assess_risk(self, input: VendorOnboardingInput) -> RiskAssessment:
        vendor_id = self._id(input.vendor_id)
        run_id = self._id(input.run_id)
        async with self._database.session() as session:
            vendor = await VendorRepository(session).get(vendor_id, for_update=True)
            if vendor is None:
                raise ValueError("vendor does not exist")
            score = 25 if vendor.jurisdiction in {"US", "GB", "CA"} else 65
            vendor.risk_score = score
            assessment = RiskAssessment(
                score=score,
                summary=f"Deterministic jurisdiction risk score: {score}.",
            )
            citations = await self._policy_retriever.retrieve(
                session, "vendor onboarding approval policy"
            )
            if self._mcp_adapter is not None:
                run = await WorkflowRunRepository(session).get(run_id)
                if run is None:
                    raise ValueError("workflow run does not exist")
                await self._mcp_adapter.call(
                    session,
                    context=MCPExecutionContext(
                        workflow_run_id=str(run_id),
                        agent_id=str(run.agent_id),
                        correlation_id=f"{run_id}:risk",
                    ),
                    tool_name="policy.search",
                    arguments={"query": "vendor onboarding approval policy", "limit": 10},
                    idempotency_key=f"{run_id}:policy-search",
                )
            citation_evidence = [
                {
                    "document_id": citation.document_id,
                    "title": citation.title,
                    "source_uri": citation.source_uri,
                }
                for citation in citations
            ]
            explanation_available = True
            try:
                await ModelEvidenceService(self._model_provider).explain(
                    session,
                    workflow_run_id=run_id,
                    prompt=self._risk_explanation_prompt(
                        jurisdiction=vendor.jurisdiction,
                        score=score,
                        policy_context="\n".join(citation.content for citation in citations),
                    ),
                    correlation_id=f"{run_id}:risk-assessment",
                )
            except RuntimeError:
                explanation_available = False
            await self._append_event(
                WorkflowRunRepository(session),
                run_id,
                "risk.assessed",
                assessment.summary,
                asdict(assessment),
                sequence=20,
            )
            await self._append_event(
                WorkflowRunRepository(session),
                run_id,
                "risk.policy_context",
                "Risk explanation grounded in retrieved policy evidence.",
                {
                    "citations": citation_evidence,
                    "model_explanation_available": explanation_available,
                },
                sequence=25,
            )
            await self._audit(
                session, run_id, "vendor.risk.assess", "vendor", vendor_id, assessment.summary
            )
            return assessment

    @staticmethod
    def _risk_explanation_prompt(*, jurisdiction: str, score: int, policy_context: str) -> str:
        return (
            "Provide a concise explanation of the deterministic vendor risk result. "
            "Do not recommend or authorize an approval decision.\n"
            f"Jurisdiction: {jurisdiction}\n"
            f"Deterministic risk score: {score}\n"
            f"Policy evidence:\n{policy_context}"
        )

    @activity.defn(name="vendor_onboarding.request_approval")
    async def request_approval(
        self, input: VendorOnboardingInput, assessment: RiskAssessment
    ) -> str:
        run_id = self._id(input.run_id)
        async with self._database.session() as session:
            request = await session.scalar(
                select(ApprovalRequest).where(
                    ApprovalRequest.workflow_run_id == run_id,
                    ApprovalRequest.request_key == "final-decision",
                )
            )
            if request is None:
                request = ApprovalRequest(
                    workflow_run_id=run_id,
                    request_key="final-decision",
                    status=ApprovalStatus.PENDING,
                    summary=(
                        f"Approve vendor after deterministic risk assessment ({assessment.score})."
                    ),
                )
                session.add(request)
                await session.flush()
                APPROVAL_REQUESTS.labels("created").inc()
            run = await WorkflowRunRepository(session).get(run_id)
            if run is None:
                raise ValueError("workflow run does not exist")
            if run.status is not RunStatus.WAITING:
                run.status = RunStatus.WAITING
                ACTIVE_RUNS.labels("running").dec()
                ACTIVE_RUNS.labels("waiting").inc()
            await self._append_event(
                WorkflowRunRepository(session),
                run_id,
                "approval.requested",
                "Human approval requested.",
                {"approval_request_id": str(request.id), "risk_score": assessment.score},
                sequence=30,
            )
            await self._audit(
                session,
                run_id,
                "approval.request.create",
                "approval_request",
                request.id,
                "Human approval requested.",
            )
            return str(request.id)

    @activity.defn(name="vendor_onboarding.record_decision")
    async def record_decision(
        self, input: VendorOnboardingInput, decision: ApprovalDecisionInput
    ) -> None:
        run_id = self._id(input.run_id)
        vendor_id = self._id(input.vendor_id)
        approver_id = self._id(decision.decided_by_id)
        approval_request_id = self._id(decision.approval_request_id)
        async with self._database.session() as session:
            request = await session.scalar(
                select(ApprovalRequest)
                .where(
                    ApprovalRequest.id == approval_request_id,
                    ApprovalRequest.workflow_run_id == run_id,
                )
                .with_for_update()
            )
            run = await WorkflowRunRepository(session).get(run_id)
            vendor = await VendorRepository(session).get(vendor_id, for_update=True)
            if request is None or run is None or vendor is None:
                raise ValueError("workflow state does not exist")
            existing = await session.scalar(
                select(ApprovalDecision).where(
                    ApprovalDecision.approval_request_id == request.id,
                    ApprovalDecision.idempotency_key == decision.idempotency_key,
                )
            )
            approved = decision.decision is ApprovalDecisionType.APPROVED
            status = ApprovalStatus.APPROVED if approved else ApprovalStatus.REJECTED
            if existing is not None:
                if (
                    existing.decided_by_id != approver_id
                    or existing.decision is not status
                    or existing.rationale != decision.rationale
                ):
                    raise ValueError(
                        "approval idempotency key conflicts with the persisted decision"
                    )
                return
            if (
                request.status is not ApprovalStatus.PENDING
                or request.version != decision.expected_version
            ):
                raise ValueError("approval request is no longer pending at the expected version")
            session.add(
                ApprovalDecision(
                    approval_request_id=request.id,
                    decided_by_id=approver_id,
                    decision=status,
                    rationale=decision.rationale,
                    idempotency_key=decision.idempotency_key,
                )
            )
            request.status = status
            vendor.status = VendorStatus.APPROVED if approved else VendorStatus.REJECTED
            run.status = RunStatus.SUCCEEDED if approved else RunStatus.REJECTED
            APPROVAL_DECISIONS.labels(decision.decision.value).inc()
            RUN_OUTCOMES.labels(run.status.value).inc()
            ACTIVE_RUNS.labels("waiting").dec()
            run.result_summary = {
                "decision": decision.decision.value,
                "risk_score": vendor.risk_score,
            }
            run.completed_at = activity.info().started_time
            if approved:
                approved_vendor = await session.scalar(
                    select(ApprovedVendor).where(ApprovedVendor.vendor_id == vendor_id)
                )
                if approved_vendor is None:
                    session.add(
                        ApprovedVendor(
                            vendor_id=vendor_id,
                            workflow_run_id=run_id,
                            approval_request_id=request.id,
                        )
                    )
                if self._mcp_adapter is not None:
                    await self._mcp_adapter.call(
                        session,
                        context=MCPExecutionContext(
                            workflow_run_id=str(run_id),
                            agent_id=str(run.agent_id),
                            correlation_id=f"{run_id}:approval",
                        ),
                        tool_name="email.send",
                        arguments={
                            "recipient": vendor.contact_email,
                            "subject": "Synthetic vendor approval",
                            "body": "This is a simulated approval notification.",
                        },
                        idempotency_key=f"{run_id}:approval-email",
                    )
            await self._append_event(
                WorkflowRunRepository(session),
                run_id,
                "approval.decided",
                f"Vendor {decision.decision.value} by human approver.",
                {"decision": decision.decision.value, "approval_request_id": str(request.id)},
                sequence=40,
            )
            await self._audit(
                session,
                run_id,
                "approval.decision.record",
                "approval_request",
                request.id,
                f"Vendor {decision.decision.value} by human approver.",
            )

    @activity.defn(name="vendor_onboarding.cancel_review")
    async def cancel_review(self, input: VendorOnboardingInput) -> None:
        run_id = self._id(input.run_id)
        async with self._database.session() as session:
            run = await WorkflowRunRepository(session).get(run_id)
            if run is None:
                raise ValueError("workflow run does not exist")
            if run.status in {RunStatus.SUCCEEDED, RunStatus.REJECTED, RunStatus.CANCELLED}:
                return
            approval = await session.scalar(
                select(ApprovalRequest)
                .where(ApprovalRequest.workflow_run_id == run_id)
                .with_for_update()
            )
            if approval is not None and approval.status is ApprovalStatus.PENDING:
                approval.status = ApprovalStatus.CANCELLED
            run.status = RunStatus.CANCELLED
            run.completed_at = activity.info().started_time
            RUN_OUTCOMES.labels("cancelled").inc()
            if approval is not None:
                ACTIVE_RUNS.labels("waiting").dec()
            else:
                ACTIVE_RUNS.labels("running").dec()
            await self._append_event(
                WorkflowRunRepository(session),
                run_id,
                "review.cancelled",
                "Review cancelled.",
                {},
                sequence=50,
            )
            await self._audit(
                session, run_id, "workflow.cancel", "workflow_run", run_id, "Review cancelled."
            )

    async def _append_event(
        self,
        runs: WorkflowRunRepository,
        run_id: uuid.UUID,
        event_type: str,
        summary: str,
        payload: dict[str, object],
        *,
        sequence: int,
    ) -> None:
        existing = await runs.events(run_id)
        if any(event.sequence == sequence for event in existing):
            return
        await runs.append_event(
            WorkflowEvent(
                workflow_run_id=run_id,
                sequence=sequence,
                event_type=event_type,
                summary=summary,
                payload=payload,
            )
        )

    async def _audit(
        self,
        session: AsyncSession,
        run_id: uuid.UUID,
        action: str,
        resource_type: str,
        resource_id: uuid.UUID,
        summary: str,
    ) -> None:
        # Activity attempts can repeat, so audit identity is deterministic per durable transition.
        repository = AuditEventRepository(session)
        key = f"{run_id}:{action}"
        if await repository.get_by_idempotency_key(key) is None:
            await repository.append(
                AuditEvent(
                    workflow_run_id=run_id,
                    actor_id=None,
                    action=action,
                    resource_type=resource_type,
                    resource_id=resource_id,
                    idempotency_key=key,
                    summary=summary,
                    evidence={},
                )
            )
