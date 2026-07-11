import os
import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.orm.exc import StaleDataError

from agents_should_survive_failure.persistence.models import (
    Agent,
    AuditEvent,
    PolicyDocument,
    RunStatus,
    User,
    Vendor,
    VendorStatus,
    WorkflowEvent,
    WorkflowRun,
)
from agents_should_survive_failure.persistence.repositories import (
    AuditEventRepository,
    VendorRepository,
    WorkflowRunRepository,
)
from agents_should_survive_failure.persistence.seed import seed_id
from agents_should_survive_failure.persistence.session import Database

EXPECTED_TABLES = {
    "agents",
    "approval_decisions",
    "approval_requests",
    "approved_vendors",
    "audit_events",
    "evaluation_cases",
    "evaluation_results",
    "evaluation_runs",
    "model_calls",
    "policy_documents",
    "tool_definitions",
    "tool_invocations",
    "users",
    "vendor_documents",
    "vendors",
    "workflow_events",
    "workflow_runs",
}


@pytest_asyncio.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    database_engine = create_async_engine(
        os.getenv(
            "INTEGRATION_DATABASE_URL",
            "postgresql+asyncpg://agents:local-development-only@127.0.0.1:5432/agents",
        )
    )
    try:
        yield database_engine
    finally:
        await database_engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_migration_created_complete_schema_and_seed_data(engine: AsyncEngine) -> None:
    async with engine.connect() as connection:
        table_names = await connection.run_sync(lambda sync: set(inspect(sync).get_table_names()))
        extension_version = await connection.scalar(
            text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
        )
        user = await connection.scalar(
            select(User.id).where(User.id == seed_id("user:demo-operator"))
        )
        agent = await connection.scalar(
            select(Agent.id).where(Agent.id == seed_id("agent:vendor-onboarding:v1"))
        )

    assert table_names >= EXPECTED_TABLES
    assert isinstance(extension_version, str)
    assert user == seed_id("user:demo-operator")
    assert agent == seed_id("agent:vendor-onboarding:v1")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_repositories_persist_run_events_and_audit(engine: AsyncEngine) -> None:
    database = Database(engine)
    reference = f"vendor-{uuid.uuid4()}"

    async with database.session() as session:
        vendor = await VendorRepository(session).add(
            Vendor(
                external_reference=reference,
                legal_name="Repository Test Vendor",
                jurisdiction="US",
                contact_email="repository@example.invalid",
                status=VendorStatus.SUBMITTED,
            )
        )
        run = await WorkflowRunRepository(session).add(
            WorkflowRun(
                agent_id=seed_id("agent:vendor-onboarding:v1"),
                vendor_id=vendor.id,
                requested_by_id=seed_id("user:demo-operator"),
                workflow_type="vendor_onboarding",
                temporal_workflow_id=f"workflow-{uuid.uuid4()}",
                idempotency_key=f"run-{uuid.uuid4()}",
                status=RunStatus.PENDING,
                input_summary={"vendor_id": str(vendor.id)},
            )
        )
        event = await WorkflowRunRepository(session).append_event(
            WorkflowEvent(
                workflow_run_id=run.id,
                sequence=1,
                event_type="run.created",
                summary="Vendor-onboarding run created.",
                payload={},
            )
        )
        audit = await AuditEventRepository(session).append(
            AuditEvent(
                workflow_run_id=run.id,
                actor_id=seed_id("user:demo-operator"),
                action="workflow_run.create",
                resource_type="workflow_run",
                resource_id=run.id,
                idempotency_key=f"audit-{uuid.uuid4()}",
                summary="Created vendor-onboarding run.",
                evidence={"event_id": str(event.id)},
            )
        )

    async with database.session() as session:
        loaded_vendor = await VendorRepository(session).get_by_external_reference(reference)
        loaded_run = await WorkflowRunRepository(session).get_by_idempotency_key(
            run.idempotency_key
        )
        events = await WorkflowRunRepository(session).events(run.id)
        audit_events = await AuditEventRepository(session).for_run(run.id)

    assert loaded_vendor is not None and loaded_vendor.id == vendor.id
    assert loaded_run is not None and loaded_run.id == run.id
    assert [item.id for item in events] == [event.id]
    assert [item.id for item in audit_events] == [audit.id]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_database_constraints_reject_duplicate_and_invalid_vendor(
    engine: AsyncEngine,
) -> None:
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    reference = f"constraint-{uuid.uuid4()}"

    async with sessions.begin() as session:
        session.add(
            Vendor(
                external_reference=reference,
                legal_name="Valid Vendor",
                jurisdiction="GB",
                contact_email="valid@example.invalid",
                status=VendorStatus.SUBMITTED,
            )
        )

    with pytest.raises(IntegrityError):
        async with sessions.begin() as session:
            session.add(
                Vendor(
                    external_reference=reference,
                    legal_name="Duplicate Vendor",
                    jurisdiction="GB",
                    contact_email="duplicate@example.invalid",
                    status=VendorStatus.SUBMITTED,
                )
            )

    with pytest.raises(IntegrityError):
        async with sessions.begin() as session:
            session.add(
                Vendor(
                    external_reference=f"risk-{uuid.uuid4()}",
                    legal_name="Invalid Risk Vendor",
                    jurisdiction="GB",
                    contact_email="risk@example.invalid",
                    status=VendorStatus.UNDER_REVIEW,
                    risk_score=101,
                )
            )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_vendor_updates_use_optimistic_concurrency(engine: AsyncEngine) -> None:
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions.begin() as session:
        vendor = Vendor(
            external_reference=f"concurrency-{uuid.uuid4()}",
            legal_name="Concurrent Vendor",
            jurisdiction="CA",
            contact_email="concurrency@example.invalid",
            status=VendorStatus.SUBMITTED,
        )
        session.add(vendor)

    first = sessions()
    second = sessions()
    try:
        first_vendor = await first.get(Vendor, vendor.id)
        second_vendor = await second.get(Vendor, vendor.id)
        assert first_vendor is not None and second_vendor is not None
        first_vendor.status = VendorStatus.UNDER_REVIEW
        await first.commit()
        second_vendor.status = VendorStatus.REJECTED
        with pytest.raises(StaleDataError):
            await second.commit()
    finally:
        await first.close()
        await second.close()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_pgvector_cosine_similarity_orders_policy_chunks(engine: AsyncEngine) -> None:
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    marker = uuid.uuid4().hex
    async with sessions.begin() as session:
        near = PolicyDocument(
            title="Near policy",
            source_uri=f"seed://{marker}/near",
            chunk_index=0,
            content="Near vector",
            content_sha256="a" * 64,
            embedding_model="deterministic-test-8d",
            embedding=[1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
            metadata_={},
        )
        far = PolicyDocument(
            title="Far policy",
            source_uri=f"seed://{marker}/far",
            chunk_index=0,
            content="Far vector",
            content_sha256="b" * 64,
            embedding_model="deterministic-test-8d",
            embedding=[0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
            metadata_={},
        )
        session.add_all([near, far])

    async with sessions() as session:
        result = await session.scalars(
            select(PolicyDocument)
            .where(PolicyDocument.source_uri.like(f"seed://{marker}/%"))
            .order_by(
                PolicyDocument.embedding.cosine_distance([1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0])
            )
        )
        ordered = result.all()

    assert [document.id for document in ordered] == [near.id, far.id]
