import uuid
from collections.abc import Sequence
from types import SimpleNamespace
from typing import Any, cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from agents_should_survive_failure.evaluation import EvaluationRunner
from agents_should_survive_failure.evaluation_scenarios import (
    EvaluationCaseDefinition,
    EvaluationSuiteDefinition,
    load_packaged_evaluation_suite,
)
from agents_should_survive_failure.persistence.models import (
    EvaluationResult,
    EvaluationRun,
    EvaluationStatus,
    InvocationStatus,
    VendorStatus,
)
from agents_should_survive_failure.tool_gateway import (
    ToolDeniedError,
    ToolGateway,
    ToolInputError,
    ToolInvocationConflictError,
)


class FakeScalars:
    def __init__(self, values: list[object]) -> None:
        self._values = values

    def __iter__(self):  # type: ignore[no-untyped-def]
        return iter(self._values)

    def all(self) -> list[object]:
        return self._values


class FakeExecuteResult:
    def __init__(self, value: object | None) -> None:
        self._value = value

    def scalar_one_or_none(self) -> object | None:
        return self._value


class FakeSession:
    def __init__(
        self,
        scalar_values: list[object | None],
        cases: Sequence[object] | None = None,
        agent: object | None = None,
        inserted_evaluation_run: bool = True,
    ) -> None:
        self._scalar_values = scalar_values
        self._cases = list(cases or [])
        self._agent = agent
        self._inserted_evaluation_run = inserted_evaluation_run
        self._evaluation_run: object | None = None
        self._last_tool_id: object | None = None
        self.added: list[object] = []

    async def scalar(self, statement: object) -> object | None:
        rendered = str(statement)
        if "run_tool_grant_snapshots" in rendered:
            return SimpleNamespace(policy_version="1", policy_hash="test")
        if "tool_run_bindings" in rendered:
            if self._last_tool_id is None:
                return None
            return SimpleNamespace(tool_definition_id=self._last_tool_id)
        if not self._scalar_values:
            return None
        value = self._scalar_values.pop(0)
        if "tool_definitions" in rendered and value is not None:
            self._last_tool_id = cast(Any, value).id
        return value

    async def scalars(self, statement: object) -> FakeScalars:
        return FakeScalars(self._cases)

    async def get(self, model: object, identifier: object) -> object | None:
        if model is EvaluationRun:
            if self._evaluation_run is None:
                self._evaluation_run = SimpleNamespace(
                    id=identifier,
                    suite_slug="vendor-onboarding-phase-b",
                    suite_version="1.0.0",
                    suite_schema_version="1",
                    dataset_sha256="",
                    status=EvaluationStatus.RUNNING,
                    configuration={},
                    completed_at=None,
                )
            return self._evaluation_run
        del identifier
        return self._agent

    def add(self, item: object) -> None:
        self.added.append(item)

    async def flush(self) -> None:
        return None

    async def execute(self, statement: object) -> FakeExecuteResult:
        if "INSERT INTO evaluation_runs" in str(statement) and self._inserted_evaluation_run:
            return FakeExecuteResult(uuid.uuid4())
        return FakeExecuteResult(None)


def persisted_case(
    suite: EvaluationSuiteDefinition, definition: EvaluationCaseDefinition
) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        suite_slug=suite.suite_slug,
        suite_version=suite.suite_version,
        schema_version=suite.schema_version,
        slug=definition.slug,
        version=definition.case_version,
        workflow_type=suite.workflow_type,
        title=definition.title,
        description=definition.description,
        scenario_type=definition.scenario_type.value,
        input_data=definition.input.model_dump(mode="json"),
        setup=definition.setup.model_dump(mode="json"),
        driver=definition.driver.model_dump(mode="json"),
        expected_outcome=definition.expected_outcome.model_dump(mode="json"),
        evidence_requirements=[item.value for item in definition.evidence_requirements],
        content_sha256=suite.case_content_sha256(definition),
        reviewed_by=suite.reviewed_by,
        reviewed_at=suite.reviewed_at,
        enabled=True,
    )


@pytest.mark.asyncio
async def test_evaluation_runner_records_passing_and_failing_cases() -> None:
    suite = load_packaged_evaluation_suite()
    cases = [persisted_case(suite, definition) for definition in suite.cases]
    cases[1].title = "Corrupted persisted title with a stale stored digest"
    session = FakeSession([None], cases)

    run = await EvaluationRunner().run_vendor_onboarding(
        cast(AsyncSession, session), requested_by_id=uuid.uuid4(), idempotency_key="evaluation"
    )

    assert run.status is EvaluationStatus.FAILED
    assert len(session.added) == 24
    results = [item for item in session.added if isinstance(item, EvaluationResult)]
    assert len(results) == 24
    assert all(result.actual_outcome["workflow_executed"] is False for result in results)
    assert results[0].status.value == "passed"
    assert results[1].failure_category == "catalog_persistence_mismatch"
    assert "persisted_contract_differs" in results[1].actual_outcome["mismatch_reasons"]
    assert "reconstructed_case_hash_mismatch" in results[1].actual_outcome["mismatch_reasons"]


@pytest.mark.asyncio
async def test_evaluation_runner_reuses_an_idempotent_run() -> None:
    existing = SimpleNamespace(id=uuid.uuid4())
    session = FakeSession([existing], inserted_evaluation_run=False)

    from agents_should_survive_failure.evaluation import evaluation_request_fingerprint

    existing.request_fingerprint = evaluation_request_fingerprint(load_packaged_evaluation_suite())
    run = await EvaluationRunner().run_vendor_onboarding(
        cast(AsyncSession, session), requested_by_id=uuid.uuid4(), idempotency_key="release-1"
    )

    assert run is existing
    assert not session.added


@pytest.mark.asyncio
async def test_tool_gateway_denies_and_reuses_idempotent_invocation() -> None:
    gateway = ToolGateway()
    agent_id = uuid.uuid4()
    tool = SimpleNamespace(
        id=uuid.uuid4(),
        name="vendor_database_query",
        version="v1",
        enabled=True,
        permissions=["vendors:read"],
        approval_required=False,
        timeout_seconds=10,
    )
    denied = FakeSession([tool, None], agent=None)
    with pytest.raises(ToolDeniedError):
        await gateway.invoke_vendor_lookup(
            cast(AsyncSession, denied),
            workflow_run_id=str(uuid.uuid4()),
            agent_id=str(agent_id),
            external_reference="vendor",
            idempotency_key="lookup",
        )

    existing = SimpleNamespace(
        id=uuid.uuid4(), result_summary={"found": False}, status=InvocationStatus.SUCCEEDED
    )
    tool = SimpleNamespace(
        id=uuid.uuid4(),
        name="vendor_database_query",
        version="v1",
        enabled=True,
        permissions=["vendors:read"],
        approval_required=False,
        timeout_seconds=10,
    )
    existing.arguments = {"external_reference": "vendor"}
    existing.argument_fingerprint = "legacy"
    agent = SimpleNamespace(name="vendor-onboarding", version="1", configuration={})
    reused = FakeSession([tool, existing], agent=agent)
    result = await gateway.invoke_vendor_lookup(
        cast(AsyncSession, reused),
        workflow_run_id=str(uuid.uuid4()),
        agent_id=str(agent_id),
        external_reference="vendor",
        idempotency_key="lookup",
    )

    assert result.result == {"found": False}
    assert result.invocation_id == str(existing.id)


@pytest.mark.asyncio
async def test_tool_gateway_records_found_vendor() -> None:
    tool = SimpleNamespace(
        id=uuid.uuid4(),
        name="vendor_database_query",
        version="v1",
        enabled=True,
        permissions=["vendors:read"],
        approval_required=False,
        timeout_seconds=10,
    )
    vendor = SimpleNamespace(id=uuid.uuid4(), status=VendorStatus.SUBMITTED)
    agent = SimpleNamespace(name="vendor-onboarding", version="1", configuration={})
    session = FakeSession([tool, None, vendor], agent=agent)

    result = await ToolGateway().invoke_vendor_lookup(
        cast(AsyncSession, session),
        workflow_run_id=str(uuid.uuid4()),
        agent_id=str(uuid.uuid4()),
        external_reference="vendor",
        idempotency_key="lookup",
    )

    assert result.result["found"] is True
    assert len(session.added) == 1


@pytest.mark.asyncio
async def test_tool_gateway_validates_inputs_and_idempotency_arguments() -> None:
    gateway = ToolGateway()
    agent = SimpleNamespace(name="vendor-onboarding", version="1", configuration={})
    with pytest.raises(ToolInputError):
        await gateway.invoke_vendor_lookup(
            cast(
                AsyncSession,
                FakeSession(
                    [
                        SimpleNamespace(
                            id=uuid.uuid4(),
                            name="vendor_database_query",
                            version="v1",
                            enabled=True,
                            permissions=["vendors:read"],
                            approval_required=False,
                            timeout_seconds=10,
                        ),
                        None,
                    ],
                    agent=agent,
                ),
            ),
            workflow_run_id=str(uuid.uuid4()),
            agent_id=str(uuid.uuid4()),
            external_reference="invalid reference with spaces",
            idempotency_key="lookup",
        )

    tool = SimpleNamespace(
        id=uuid.uuid4(),
        name="vendor_database_query",
        version="v1",
        enabled=True,
        permissions=["vendors:read"],
        approval_required=False,
        timeout_seconds=10,
    )
    existing = SimpleNamespace(
        id=uuid.uuid4(),
        result_summary={"found": False},
        arguments={"external_reference": "other"},
        argument_fingerprint="legacy",
        status=InvocationStatus.SUCCEEDED,
    )
    with pytest.raises(ToolInvocationConflictError):
        await gateway.invoke_vendor_lookup(
            cast(AsyncSession, FakeSession([tool, existing], agent=agent)),
            workflow_run_id=str(uuid.uuid4()),
            agent_id=str(uuid.uuid4()),
            external_reference="vendor",
            idempotency_key="lookup",
        )


@pytest.mark.asyncio
async def test_tool_capabilities_are_negotiated_from_platform_agent_policy() -> None:
    permitted = SimpleNamespace(
        name="vendor_database_query",
        version="v1",
        enabled=True,
        permissions=["vendors:read"],
        tool_definition_id=uuid.uuid4(),
    )
    agent = SimpleNamespace(
        name="vendor-onboarding",
        version="1",
        configuration={"tool_permissions": ["other:read"]},
    )

    capabilities = await ToolGateway().capabilities(
        cast(AsyncSession, FakeSession([], [permitted], agent=agent)),
        agent_id=str(uuid.uuid4()),
    )

    assert [(item.name, item.permissions) for item in capabilities] == [
        ("vendor_database_query", ("vendors:read",))
    ]
