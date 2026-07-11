import os
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agents_should_survive_failure.evaluation import EvaluationRunner
from agents_should_survive_failure.model_evidence import ModelEvidenceService
from agents_should_survive_failure.persistence.models import (
    EvaluationResult,
    EvaluationStatus,
    ModelCall,
    RunStatus,
    Vendor,
    VendorStatus,
    WorkflowRun,
)
from agents_should_survive_failure.persistence.seed import seed_id
from agents_should_survive_failure.policy import PolicyRetriever
from agents_should_survive_failure.providers import DeterministicModelProvider
from agents_should_survive_failure.tool_gateway import ToolDeniedError, ToolGateway


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
        async with sessions() as session:
            with pytest.raises(ToolDeniedError):
                await ToolGateway().invoke_vendor_lookup(
                    session,
                    workflow_run_id=str(uuid.uuid4()),
                    agent_id=str(uuid.uuid4()),
                    external_reference="not-used",
                    idempotency_key="denied",
                )
    finally:
        await engine.dispose()
