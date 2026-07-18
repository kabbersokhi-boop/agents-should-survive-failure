"""Retry-safe transaction-owning activities for refunds."""

import uuid
from decimal import Decimal

from sqlalchemy import select
from temporalio import activity

from agents_should_survive_failure.mcp_adapter import GovernedMCPAdapter, MCPExecutionContext
from agents_should_survive_failure.model_evidence import ModelEvidenceService
from agents_should_survive_failure.persistence.models import (
    ApprovalDecision,
    ApprovalRequest,
    ApprovalStatus,
    RefundDecision,
    RefundEmail,
    RefundProjection,
    RunStatus,
    WorkflowRun,
)
from agents_should_survive_failure.persistence.session import Database
from agents_should_survive_failure.providers import DeterministicModelProvider, ModelProvider
from agents_should_survive_failure.workflows.contracts import (
    ApprovalDecisionInput,
    ApprovalDecisionType,
    RefundWorkflowInput,
)


class RefundActivities:
    def __init__(
        self,
        database: Database,
        model_provider: ModelProvider | None = None,
        mcp_adapter: GovernedMCPAdapter | None = None,
    ) -> None:
        self._database = database
        self._provider = model_provider or DeterministicModelProvider()
        self._mcp = mcp_adapter

    @staticmethod
    def _id(value: str) -> uuid.UUID:
        return uuid.UUID(value)

    async def _call(
        self, session, input: RefundWorkflowInput, name: str, arguments: dict[str, object], key: str
    ):
        if self._mcp is None:
            raise RuntimeError("governed tool gateway is required")
        run = await session.get(WorkflowRun, self._id(input.run_id))
        if run is None:
            raise ValueError("workflow run does not exist")
        return await self._mcp.call(
            session,
            context=MCPExecutionContext(input.run_id, str(run.agent_id), f"{input.run_id}:{name}"),
            tool_name=name,
            arguments=arguments,
            idempotency_key=key,
        )

    @activity.defn(name="refund.retrieve_order_evidence")
    async def retrieve_order_evidence(self, input: RefundWorkflowInput) -> dict[str, object]:
        async with self._database.session() as session:
            return (
                await self._call(
                    session,
                    input,
                    "order.details",
                    {"order_id": input.order_id},
                    f"{input.run_id}:order-details",
                )
            ).result

    @activity.defn(name="refund.retrieve_policy_evidence")
    async def retrieve_policy_evidence(self, input: RefundWorkflowInput) -> dict[str, object]:
        async with self._database.session() as session:
            return (
                await self._call(
                    session,
                    input,
                    "refund.policy",
                    {"reason": input.reason},
                    f"{input.run_id}:refund-policy",
                )
            ).result

    @activity.defn(name="refund.calculate_refund_risk")
    async def calculate_refund_risk(
        self, input: RefundWorkflowInput, order: dict[str, object], policy: dict[str, object]
    ) -> dict[str, object]:
        del policy
        amount = Decimal(input.amount)
        score = min(
            100,
            20
            + (40 if amount >= Decimal("500") else 10)
            + (25 if order["status"] != "delivered" else 0),
        )
        return {"score": score, "summary": f"Deterministic refund risk score: {score}."}

    @activity.defn(name="refund.explain_refund_risk")
    async def explain_refund_risk(
        self, input: RefundWorkflowInput, risk: dict[str, object], policy: dict[str, object]
    ) -> str:
        async with self._database.session() as session:
            response = await ModelEvidenceService(self._provider).explain(
                session,
                workflow_run_id=self._id(input.run_id),
                prompt=(
                    f"Explain refund risk {risk['score']} using policy evidence "
                    f"{policy['citations']} only."
                ),
                correlation_id=f"{input.run_id}:refund-risk",
            )
            return response.summary[:1000]

    @activity.defn(name="refund.request_approval")
    async def request_approval(
        self, input: RefundWorkflowInput, risk: dict[str, object], explanation: str
    ) -> str:
        async with self._database.session() as session:
            request = await session.scalar(
                select(ApprovalRequest).where(
                    ApprovalRequest.workflow_run_id == self._id(input.run_id),
                    ApprovalRequest.request_key == "refund-decision",
                )
            )
            if request is None:
                request = ApprovalRequest(
                    workflow_run_id=self._id(input.run_id),
                    request_key="refund-decision",
                    status=ApprovalStatus.PENDING,
                    summary=(
                        f"Approve refund of {input.amount}; risk {risk['score']}. {explanation}"
                    ),
                )
                session.add(request)
                await session.flush()
            run = await session.get(WorkflowRun, self._id(input.run_id))
            if run is not None:
                run.status = RunStatus.WAITING
            return str(request.id)

    @activity.defn(name="refund.commit_refund_decision")
    async def commit_refund_decision(
        self,
        input: RefundWorkflowInput,
        risk: dict[str, object],
        explanation: str,
        decision: ApprovalDecisionInput,
    ) -> None:
        async with self._database.session() as session:
            run_id = self._id(input.run_id)
            if (
                await session.scalar(
                    select(RefundDecision).where(
                        RefundDecision.workflow_run_id == run_id,
                        RefundDecision.idempotency_key == decision.idempotency_key,
                    )
                )
                is not None
            ):
                return
            request = await session.scalar(
                select(ApprovalRequest).where(
                    ApprovalRequest.workflow_run_id == run_id,
                    ApprovalRequest.request_key == "refund-decision",
                )
            )
            if request is None:
                raise ValueError("refund approval request does not exist")
            decision_status = (
                ApprovalStatus.APPROVED
                if decision.decision is ApprovalDecisionType.APPROVED
                else ApprovalStatus.REJECTED
            )
            session.add(
                ApprovalDecision(
                    approval_request_id=request.id,
                    decided_by_id=self._id(decision.decided_by_id),
                    decision=decision_status,
                    rationale=decision.rationale,
                    idempotency_key=decision.idempotency_key,
                )
            )
            request.status = decision_status
            session.add(
                RefundDecision(
                    workflow_run_id=run_id,
                    refund_id=input.refund_id,
                    order_id=input.order_id,
                    amount=Decimal(input.amount),
                    decision=decision.decision.value,
                    risk_score=int(risk["score"]),
                    rationale=decision.rationale or explanation,
                    idempotency_key=decision.idempotency_key,
                )
            )
            if (
                decision.decision is ApprovalDecisionType.APPROVED
                and await session.scalar(
                    select(RefundProjection).where(RefundProjection.refund_id == input.refund_id)
                )
                is None
            ):
                session.add(
                    RefundProjection(
                        workflow_run_id=run_id,
                        refund_id=input.refund_id,
                        order_id=input.order_id,
                        customer_id=input.customer_id,
                        amount=Decimal(input.amount),
                        status="refunded",
                        idempotency_key=f"{input.run_id}:refund-projection",
                    )
                )
            run = await session.get(WorkflowRun, run_id)
            if run is not None:
                run.status = (
                    RunStatus.SUCCEEDED
                    if decision.decision is ApprovalDecisionType.APPROVED
                    else RunStatus.REJECTED
                )

    @activity.defn(name="refund.send_refund_notification")
    async def send_refund_notification(self, input: RefundWorkflowInput) -> None:
        async with self._database.session() as session:
            key = f"{input.run_id}:refund-email"
            if (
                await session.scalar(
                    select(RefundEmail).where(
                        RefundEmail.workflow_run_id == self._id(input.run_id),
                        RefundEmail.idempotency_key == key,
                    )
                )
                is None
            ):
                session.add(
                    RefundEmail(
                        workflow_run_id=self._id(input.run_id),
                        customer_id=input.customer_id,
                        idempotency_key=key,
                        status="simulated",
                    )
                )
            await self._call(
                session,
                input,
                "email.send",
                {
                    "recipient": f"{input.customer_id}@example.invalid",
                    "subject": "Your refund was approved",
                    "body": "Your synthetic refund has been approved.",
                },
                key,
            )
