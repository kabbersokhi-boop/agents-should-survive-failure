import os
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agents_should_survive_failure.evaluation import EvaluationRunner
from agents_should_survive_failure.model_evidence import ModelEvidenceService
from agents_should_survive_failure.persistence.models import (
    ApprovalDecision,
    ApprovalRequest,
    ApprovalStatus,
    EvaluationResult,
    EvaluationStatus,
    InvocationStatus,
    ModelCall,
    RunStatus,
    ToolDefinition,
    ToolInvocation,
    ToolRunBinding,
    Vendor,
    VendorStatus,
    WorkflowRun,
)
from agents_should_survive_failure.persistence.seed import seed_id
from agents_should_survive_failure.persistence.session import Database
from agents_should_survive_failure.policy import PolicyRetriever
from agents_should_survive_failure.providers import DeterministicModelProvider
from agents_should_survive_failure.tool_gateway import (
    ToolApprovalRequiredError,
    ToolGateway,
    ToolVersionMismatchError,
)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_policy_tool_and_evaluation_services_persist_evidence() -> None:
    engine = create_async_engine(
        os.getenv(
            "INTEGRATION_DATABASE_URL",
            "postgresql+asyncpg://agents:local-development-only@127.0.0.1:5432/agents",
        )
    )
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    reference = f"phase4-{uuid.uuid4()}"
    try:
        async with sessions.begin() as session:
            vendor = Vendor(
                external_reference=reference,
                legal_name="Phase Four Vendor",
                jurisdiction="US",
                contact_email="phase4@example.invalid",
                status=VendorStatus.SUBMITTED,
            )
            session.add(vendor)
            await session.flush()
            workflow_run = WorkflowRun(
                agent_id=seed_id("agent:vendor-onboarding:v1"),
                vendor_id=vendor.id,
                requested_by_id=seed_id("user:demo-operator"),
                workflow_type="vendor_onboarding",
                temporal_workflow_id=f"phase4-{uuid.uuid4()}",
                idempotency_key=f"phase4-{uuid.uuid4()}",
                status=RunStatus.PENDING,
                input_summary={},
            )
            session.add(workflow_run)
            await session.flush()
            citations = await PolicyRetriever().retrieve(session, "vendor approval", limit=10)
            await ModelEvidenceService(DeterministicModelProvider()).explain(
                session,
                workflow_run_id=workflow_run.id,
                prompt="Explain the vendor's deterministic risk score.",
                correlation_id=f"{workflow_run.id}:model-evidence",
            )
            first = await ToolGateway().invoke_vendor_lookup(
                session,
                workflow_run_id=str(workflow_run.id),
                agent_id=str(seed_id("agent:vendor-onboarding:v1")),
                external_reference=reference,
                idempotency_key="lookup-1",
            )
            second = await ToolGateway().invoke_vendor_lookup(
                session,
                workflow_run_id=str(workflow_run.id),
                agent_id=str(seed_id("agent:vendor-onboarding:v1")),
                external_reference=reference,
                idempotency_key="lookup-1",
            )
            evaluation = await EvaluationRunner().run_vendor_onboarding(
                session,
                requested_by_id=str(seed_id("user:demo-operator")),
                idempotency_key=f"evaluation-{uuid.uuid4()}",
            )
        assert any(citation.title == "Vendor Approval Policy" for citation in citations)
        assert first.result["found"] is True and first.invocation_id == second.invocation_id
        assert evaluation.status is EvaluationStatus.SUCCEEDED
        async with sessions() as session:
            results = await session.scalars(
                select(EvaluationResult).where(EvaluationResult.evaluation_run_id == evaluation.id)
            )
            model_calls = await session.scalars(
                select(ModelCall).where(ModelCall.workflow_run_id == workflow_run.id)
            )
            assert len(results.all()) == 1
            assert len(model_calls.all()) == 1
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_tool_gateway_denies_missing_permission() -> None:
    engine = create_async_engine(
        os.getenv(
            "INTEGRATION_DATABASE_URL",
            "postgresql+asyncpg://agents:local-development-only@127.0.0.1:5432/agents",
        )
    )
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions.begin() as session:
            vendor = Vendor(
                external_reference=f"denied-tool-{uuid.uuid4()}",
                legal_name="Denied Tool Vendor",
                jurisdiction="US",
                contact_email="denied-tool@example.invalid",
                status=VendorStatus.SUBMITTED,
            )
            session.add(vendor)
            await session.flush()
            run = WorkflowRun(
                agent_id=seed_id("agent:vendor-onboarding:v1"),
                vendor_id=vendor.id,
                requested_by_id=seed_id("user:demo-operator"),
                workflow_type="vendor_onboarding",
                temporal_workflow_id=f"denied-tool-{uuid.uuid4()}",
                idempotency_key=f"denied-tool-{uuid.uuid4()}",
                status=RunStatus.PENDING,
                input_summary={},
            )
            session.add(run)
            await session.flush()
            with pytest.raises(PermissionError):
                await ToolGateway().invoke_vendor_lookup(
                    session,
                    workflow_run_id=str(run.id),
                    agent_id=str(uuid.uuid4()),
                    external_reference="not-used",
                    idempotency_key="denied",
                )
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_denied_tool_attempt_survives_the_calling_transaction_rollback() -> None:
    engine = create_async_engine(
        os.getenv(
            "INTEGRATION_DATABASE_URL",
            "postgresql+asyncpg://agents:local-development-only@127.0.0.1:5432/agents",
        )
    )
    database = Database(engine)
    alternate_version: str
    run_id: uuid.UUID
    try:
        async with database.session() as session:
            vendor = Vendor(
                external_reference=f"durable-denial-{uuid.uuid4()}",
                legal_name="Durable Denial Vendor",
                jurisdiction="US",
                contact_email="durable-denial@example.invalid",
                status=VendorStatus.SUBMITTED,
            )
            session.add(vendor)
            await session.flush()
            run = WorkflowRun(
                agent_id=seed_id("agent:vendor-onboarding:v1"),
                vendor_id=vendor.id,
                requested_by_id=seed_id("user:demo-operator"),
                workflow_type="vendor_onboarding",
                temporal_workflow_id=f"durable-denial-{uuid.uuid4()}",
                idempotency_key=f"durable-denial-{uuid.uuid4()}",
                status=RunStatus.PENDING,
                input_summary={},
            )
            session.add(run)
            await session.flush()
            run_id = run.id
            original = await session.scalar(
                select(ToolDefinition).where(
                    ToolDefinition.name == "vendor_database_query",
                    ToolDefinition.version == "1",
                )
            )
            assert original is not None
            alternate_version = f"test-{uuid.uuid4().hex[:8]}"
            session.add(
                ToolDefinition(
                    name=original.name,
                    version=alternate_version,
                    description=original.description,
                    input_schema=original.input_schema,
                    output_schema=original.output_schema,
                    permissions=original.permissions,
                    risk_class=original.risk_class,
                    timeout_seconds=original.timeout_seconds,
                    approval_required=False,
                    enabled=True,
                )
            )
        async with database.session() as session:
            first = await ToolGateway(database).invoke(
                session,
                workflow_run_id=str(run_id),
                agent_id=str(seed_id("agent:vendor-onboarding:v1")),
                tool_name="vendor_database_query",
                tool_version="1",
                arguments={"external_reference": "not-used"},
                idempotency_key="pinned-tool-v1",
            )
        assert first.result == {"found": False}
        with pytest.raises(ToolVersionMismatchError):
            async with database.session() as session:
                await ToolGateway(database).invoke(
                    session,
                    workflow_run_id=str(run_id),
                    agent_id=str(seed_id("agent:vendor-onboarding:v1")),
                    tool_name="vendor_database_query",
                    tool_version=alternate_version,
                    arguments={"external_reference": "not-used"},
                    idempotency_key="pinned-tool-v2",
                )
        with pytest.raises(PermissionError):
            async with database.session() as session:
                await ToolGateway(database).invoke_vendor_lookup(
                    session,
                    workflow_run_id=str(run_id),
                    agent_id=str(uuid.uuid4()),
                    external_reference="not-used",
                    idempotency_key="durably-denied",
                )
        async with database.session() as session:
            invocation = await session.scalar(
                select(ToolInvocation).where(
                    ToolInvocation.workflow_run_id == run_id,
                    ToolInvocation.idempotency_key == "durably-denied",
                )
            )
            binding = await session.scalar(
                select(ToolRunBinding).where(ToolRunBinding.workflow_run_id == run_id)
            )
            version_mismatch = await session.scalar(
                select(ToolInvocation).where(
                    ToolInvocation.workflow_run_id == run_id,
                    ToolInvocation.idempotency_key == "pinned-tool-v2",
                )
            )
        assert invocation is not None
        assert invocation.status is InvocationStatus.DENIED
        assert invocation.error_category == "policy_denied"
        assert binding is not None
        assert binding.tool_name == "vendor_database_query"
        assert binding.tool_definition_id == seed_id("tool:vendor-database-query:v1")
        assert version_mismatch is not None
        assert version_mismatch.status is InvocationStatus.FAILED
        assert version_mismatch.error_category == "version_mismatch"
        with pytest.raises(RuntimeError):
            async with database.session() as session:
                await ToolGateway(database).invoke(
                    session,
                    workflow_run_id=str(run_id),
                    agent_id=str(seed_id("agent:vendor-onboarding:v1")),
                    tool_name="missing.tool",
                    tool_version="9",
                    arguments={"synthetic": True},
                    idempotency_key="unregistered-tool",
                )
        async with database.session() as session:
            unregistered = await session.scalar(
                select(ToolInvocation).where(
                    ToolInvocation.workflow_run_id == run_id,
                    ToolInvocation.idempotency_key == "unregistered-tool",
                )
            )
        assert unregistered is not None
        assert unregistered.tool_definition_id is None
        assert unregistered.requested_tool_name == "missing.tool"
        assert unregistered.requested_tool_version == "9"
        assert unregistered.error_category == "unregistered_tool"
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_governed_policy_and_synthetic_email_tools_are_durable() -> None:
    engine = create_async_engine(
        os.getenv(
            "INTEGRATION_DATABASE_URL",
            "postgresql+asyncpg://agents:local-development-only@127.0.0.1:5432/agents",
        )
    )
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    reference = f"governed-tools-{uuid.uuid4()}"
    try:
        async with sessions.begin() as session:
            vendor = Vendor(
                external_reference=reference,
                legal_name="Governed Tools Vendor",
                jurisdiction="US",
                contact_email="governed-tools@example.invalid",
                status=VendorStatus.SUBMITTED,
            )
            session.add(vendor)
            await session.flush()
            run = WorkflowRun(
                agent_id=seed_id("agent:vendor-onboarding:v1"),
                vendor_id=vendor.id,
                requested_by_id=seed_id("user:demo-operator"),
                workflow_type="vendor_onboarding",
                temporal_workflow_id=f"governed-tools-{uuid.uuid4()}",
                idempotency_key=f"governed-tools-{uuid.uuid4()}",
                status=RunStatus.WAITING,
                input_summary={},
            )
            session.add(run)
            await session.flush()
            gateway = ToolGateway()
            policy = await gateway.invoke(
                session,
                workflow_run_id=str(run.id),
                agent_id=str(run.agent_id),
                tool_name="internal_policy_search",
                tool_version="1",
                arguments={"query": "vendor approval"},
                idempotency_key="policy-search",
                correlation_id=f"{run.id}:policy-search",
            )
            assert policy.result["citations"]
            with pytest.raises(ToolApprovalRequiredError):
                await gateway.invoke(
                    session,
                    workflow_run_id=str(run.id),
                    agent_id=str(run.agent_id),
                    tool_name="synthetic_email_send",
                    tool_version="1",
                    arguments={
                        "recipient": "vendor@example.invalid",
                        "subject": "Vendor review",
                        "body": "This is synthetic only.",
                    },
                    idempotency_key="email-without-approval",
                )
            approval = ApprovalRequest(
                workflow_run_id=run.id,
                request_key="synthetic-email",
                status=ApprovalStatus.APPROVED,
                summary="Synthetic email approval.",
            )
            session.add(approval)
            await session.flush()
            session.add(
                ApprovalDecision(
                    approval_request_id=approval.id,
                    decided_by_id=seed_id("user:demo-operator"),
                    decision=ApprovalStatus.APPROVED,
                    rationale="Synthetic action approved.",
                    idempotency_key="synthetic-email-decision",
                )
            )
            first = await gateway.invoke(
                session,
                workflow_run_id=str(run.id),
                agent_id=str(run.agent_id),
                tool_name="synthetic_email_send",
                tool_version="1",
                arguments={
                    "recipient": "vendor@example.invalid",
                    "subject": "Vendor review",
                    "body": "This is synthetic only.",
                },
                idempotency_key="email-after-approval",
                correlation_id=f"{run.id}:email-after-approval",
            )
            second = await gateway.invoke(
                session,
                workflow_run_id=str(run.id),
                agent_id=str(run.agent_id),
                tool_name="synthetic_email_send",
                tool_version="1",
                arguments={
                    "recipient": "vendor@example.invalid",
                    "subject": "Vendor review",
                    "body": "This is synthetic only.",
                },
                idempotency_key="email-after-approval",
            )
        assert first == second
        async with sessions() as session:
            from agents_should_survive_failure.persistence.models import (
                SyntheticEmailMessage,
                ToolInvocation,
            )

            messages = (
                await session.scalars(
                    select(SyntheticEmailMessage).where(
                        SyntheticEmailMessage.workflow_run_id == run.id
                    )
                )
            ).all()
            calls = (
                await session.scalars(
                    select(ToolInvocation)
                    .where(ToolInvocation.workflow_run_id == run.id)
                    .order_by(ToolInvocation.created_at)
                )
            ).all()
        assert len(messages) == 1
        assert messages[0].status == "simulated"
        assert [call.status for call in calls] == [
            InvocationStatus.SUCCEEDED,
            InvocationStatus.DENIED,
            InvocationStatus.SUCCEEDED,
        ]
        assert calls[1].error_category == "approval_required"
        assert calls[2].correlation_id == f"{run.id}:email-after-approval"
    finally:
        await engine.dispose()
