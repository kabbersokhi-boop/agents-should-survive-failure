import uuid
from contextlib import asynccontextmanager
from typing import Self, cast
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from agents_should_survive_failure.persistence.models import (
    ApprovalStatus,
    AuditEvent,
    Base,
    RunStatus,
    Vendor,
    VendorStatus,
    WorkflowEvent,
    WorkflowRun,
    utc_now,
)
from agents_should_survive_failure.persistence.repositories import (
    AuditEventRepository,
    VendorRepository,
    WorkflowRunRepository,
)
from agents_should_survive_failure.persistence.seed import (
    evaluation_case_seed_rows,
    seed_database,
    seed_id,
    seed_rows,
)


def test_model_metadata_and_seeds_cover_required_schema() -> None:
    expected_tables = {
        "users",
        "auth_principals",
        "api_keys",
        "agents",
        "agent_tool_grants",
        "workflow_runs",
        "workflow_start_attempts",
        "workflow_events",
        "vendors",
        "vendor_documents",
        "approved_vendors",
        "approval_requests",
        "approval_decisions",
        "tool_definitions",
        "run_tool_grant_snapshots",
        "tool_run_bindings",
        "tool_invocations",
        "synthetic_email_messages",
        "model_calls",
        "policy_documents",
        "audit_events",
        "evaluation_runs",
        "evaluation_cases",
        "evaluation_results",
    }

    assert set(Base.metadata.tables) == expected_tables
    assert {
        "suite_slug",
        "suite_version",
        "schema_version",
        "scenario_type",
        "setup",
        "driver",
        "evidence_requirements",
        "content_sha256",
        "reviewed_at",
    }.issubset(set(Base.metadata.tables["evaluation_cases"].columns.keys()))
    assert {
        "case_slug",
        "case_version",
        "case_content_sha256",
        "expected_outcome",
        "actual_outcome",
        "failure_category",
        "duration_ms",
        "evidence_summary",
    }.issubset(set(Base.metadata.tables["evaluation_results"].columns.keys()))
    assert {
        "suite_slug",
        "suite_version",
        "suite_schema_version",
        "dataset_sha256",
    }.issubset(set(Base.metadata.tables["evaluation_runs"].columns.keys()))
    rows = seed_rows()
    evaluation_rows = [values for model, values in rows if model.__name__ == "EvaluationCase"]
    assert len(rows) == 34
    assert len(evaluation_rows) == 24
    assert {row["suite_version"] for row in evaluation_rows} == {"1.0.0"}
    assert {row["schema_version"] for row in evaluation_rows} == {"1"}
    assert len({row["content_sha256"] for row in evaluation_rows}) == 24
    assert all(len(str(row["content_sha256"])) == 64 for row in evaluation_rows)
    assert seed_id("stable") == seed_id("stable")
    assert utc_now().tzinfo is not None


class SeedResult:
    def __init__(self, mapping: dict[str, object] | None = None) -> None:
        self._mapping = mapping

    def mappings(self) -> Self:
        return self

    def one_or_none(self) -> dict[str, object] | None:
        return self._mapping


class SeedConnection:
    def __init__(
        self, *, corrupt_first_case: bool = False, disable_first_case: bool = False
    ) -> None:
        self._case_rows = [dict(values) for _, values in evaluation_case_seed_rows()]
        self._corrupt_first_case = corrupt_first_case
        self._disable_first_case = disable_first_case
        self.case_selects = 0

    async def execute(self, statement: object) -> SeedResult:
        if getattr(statement, "is_select", False):
            values = self._case_rows[self.case_selects]
            self.case_selects += 1
            if self._corrupt_first_case and self.case_selects == 1:
                values = {**values, "title": "Persisted drift"}
            if self._disable_first_case and self.case_selects == 1:
                values = {**values, "enabled": False}
            return SeedResult({key: value for key, value in values.items() if key != "id"})
        return SeedResult()


class SeedEngine:
    def __init__(self, connection: SeedConnection) -> None:
        self._connection = connection

    @asynccontextmanager
    async def begin(self):  # type: ignore[no-untyped-def]
        yield self._connection


@pytest.mark.asyncio
async def test_seed_database_verifies_all_reviewed_case_columns() -> None:
    connection = SeedConnection()

    await seed_database(cast(AsyncEngine, SeedEngine(connection)))

    assert connection.case_selects == 24


@pytest.mark.asyncio
async def test_seed_database_rejects_reviewed_case_drift() -> None:
    connection = SeedConnection(corrupt_first_case=True)

    with pytest.raises(RuntimeError, match="content changed without a new suite version"):
        await seed_database(cast(AsyncEngine, SeedEngine(connection)))

    assert connection.case_selects == 1


@pytest.mark.asyncio
async def test_seed_database_preserves_operator_case_enablement() -> None:
    connection = SeedConnection(disable_first_case=True)

    await seed_database(cast(AsyncEngine, SeedEngine(connection)))

    assert connection.case_selects == 24


def test_approval_status_exposes_only_implemented_decision_paths() -> None:
    assert "information_requested" not in {status.value for status in ApprovalStatus}


def mock_session() -> AsyncSession:
    session = Mock(spec=AsyncSession)
    session.flush = AsyncMock()
    session.scalar = AsyncMock()
    session.scalars = AsyncMock()
    session.get = AsyncMock()
    return cast(AsyncSession, session)


@pytest.mark.asyncio
async def test_vendor_repository_crud_and_filters() -> None:
    session = mock_session()
    vendor = Vendor(
        external_reference="unit-vendor",
        legal_name="Unit Vendor",
        jurisdiction="US",
        contact_email="unit@example.invalid",
        status=VendorStatus.SUBMITTED,
    )
    session.scalar.return_value = vendor  # type: ignore[attr-defined]
    scalar_result = Mock()
    scalar_result.all.return_value = [vendor]
    session.scalars.return_value = scalar_result  # type: ignore[attr-defined]
    repository = VendorRepository(session)

    assert await repository.add(vendor) is vendor
    assert await repository.get(uuid.uuid4()) is vendor
    assert await repository.get(uuid.uuid4(), for_update=True) is vendor
    assert await repository.get_by_external_reference("unit-vendor") is vendor
    assert await repository.list(status=VendorStatus.SUBMITTED, limit=5) == [vendor]
    session.add.assert_called_once_with(vendor)  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_workflow_repository_crud_filters_and_events() -> None:
    session = mock_session()
    run_id = uuid.uuid4()
    run = WorkflowRun(
        id=run_id,
        agent_id=uuid.uuid4(),
        requested_by_id=uuid.uuid4(),
        workflow_type="vendor_onboarding",
        temporal_workflow_id="unit-workflow",
        idempotency_key="unit-run",
        status=RunStatus.PENDING,
        input_summary={},
    )
    event = WorkflowEvent(
        workflow_run_id=run_id,
        sequence=1,
        event_type="unit",
        summary="Unit event.",
        payload={},
    )
    session.get.return_value = run  # type: ignore[attr-defined]
    session.scalar.return_value = run  # type: ignore[attr-defined]
    scalar_result = Mock()
    scalar_result.all.return_value = [run]
    session.scalars.return_value = scalar_result  # type: ignore[attr-defined]
    repository = WorkflowRunRepository(session)

    assert await repository.add(run) is run
    assert await repository.get(run_id) is run
    assert await repository.get_by_idempotency_key("unit-run") is run
    assert (
        await repository.get_by_principal_and_idempotency_key(
            requested_by_id=run.requested_by_id, key="unit-run"
        )
        is run
    )
    assert await repository.list(
        status=RunStatus.PENDING, workflow_type="vendor_onboarding", limit=5
    ) == [run]
    assert await repository.append_event(event) is event
    scalar_result.all.return_value = [event]
    assert await repository.events(run_id) == [event]


@pytest.mark.asyncio
async def test_audit_repository_append_and_queries() -> None:
    session = mock_session()
    run_id = uuid.uuid4()
    event = AuditEvent(
        workflow_run_id=run_id,
        action="unit.test",
        resource_type="workflow_run",
        idempotency_key="audit-key",
        summary="Unit audit event.",
        evidence={},
    )
    session.scalar.return_value = event  # type: ignore[attr-defined]
    scalar_result = Mock()
    scalar_result.all.return_value = [event]
    session.scalars.return_value = scalar_result  # type: ignore[attr-defined]
    repository = AuditEventRepository(session)

    assert await repository.append(event) is event
    assert await repository.get_by_idempotency_key("audit-key") is event
    assert await repository.for_run(run_id) == [event]
