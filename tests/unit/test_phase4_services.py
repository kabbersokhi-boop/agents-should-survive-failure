import uuid
from types import SimpleNamespace
from typing import cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from agents_should_survive_failure.evaluation import EvaluationRunner
from agents_should_survive_failure.persistence.models import EvaluationStatus, VendorStatus
from agents_should_survive_failure.tool_gateway import ToolDeniedError, ToolGateway


class FakeScalars:
    def __init__(self, values: list[object]) -> None:
        self._values = values

    def __iter__(self):  # type: ignore[no-untyped-def]
        return iter(self._values)


class FakeSession:
    def __init__(
        self, scalar_values: list[object | None], cases: list[object] | None = None
    ) -> None:
        self._scalar_values = scalar_values
        self._cases = cases or []
        self.added: list[object] = []

    async def scalar(self, statement: object) -> object | None:
        return self._scalar_values.pop(0)

    async def scalars(self, statement: object) -> FakeScalars:
        return FakeScalars(self._cases)

    def add(self, item: object) -> None:
        self.added.append(item)

    async def flush(self) -> None:
        return None


@pytest.mark.asyncio
async def test_evaluation_runner_records_passing_and_failing_cases() -> None:
    passing = SimpleNamespace(
        id=uuid.uuid4(),
        input_data={"jurisdiction": "US"},
        expected_outcome={"risk_band": "low", "requires_approval": True},
    )
    failing = SimpleNamespace(
        id=uuid.uuid4(),
        input_data={"jurisdiction": "ZZ"},
        expected_outcome={"risk_band": "low", "requires_approval": True},
    )
    session = FakeSession([None], [passing, failing])

    run = await EvaluationRunner().run_vendor_onboarding(
        cast(AsyncSession, session), requested_by_id=str(uuid.uuid4()), idempotency_key="evaluation"
    )

    assert run.status is EvaluationStatus.SUCCEEDED
    assert len(session.added) == 3


@pytest.mark.asyncio
async def test_evaluation_runner_reuses_an_idempotent_run() -> None:
    existing = SimpleNamespace(id=uuid.uuid4())
    session = FakeSession([existing])

    run = await EvaluationRunner().run_vendor_onboarding(
        cast(AsyncSession, session), requested_by_id=str(uuid.uuid4()), idempotency_key="release-1"
    )

    assert run is existing
    assert not session.added


@pytest.mark.asyncio
async def test_tool_gateway_denies_and_reuses_idempotent_invocation() -> None:
    gateway = ToolGateway()
    denied = FakeSession([None])
    with pytest.raises(ToolDeniedError):
        await gateway.invoke_vendor_lookup(
            cast(AsyncSession, denied),
            workflow_run_id=str(uuid.uuid4()),
            permissions=set(),
            external_reference="vendor",
            idempotency_key="lookup",
        )

    existing = SimpleNamespace(id=uuid.uuid4(), result_summary={"found": False})
    tool = SimpleNamespace(id=uuid.uuid4(), enabled=True, permissions=["vendors:read"])
    reused = FakeSession([tool, existing])
    result = await gateway.invoke_vendor_lookup(
        cast(AsyncSession, reused),
        workflow_run_id=str(uuid.uuid4()),
        permissions={"vendors:read"},
        external_reference="vendor",
        idempotency_key="lookup",
    )

    assert result.result == {"found": False}
    assert result.invocation_id == str(existing.id)


@pytest.mark.asyncio
async def test_tool_gateway_records_found_vendor() -> None:
    tool = SimpleNamespace(id=uuid.uuid4(), enabled=True, permissions=["vendors:read"])
    vendor = SimpleNamespace(id=uuid.uuid4(), status=VendorStatus.SUBMITTED)
    session = FakeSession([tool, None, vendor])

    result = await ToolGateway().invoke_vendor_lookup(
        cast(AsyncSession, session),
        workflow_run_id=str(uuid.uuid4()),
        permissions={"vendors:read"},
        external_reference="vendor",
        idempotency_key="lookup",
    )

    assert result.result["found"] is True
    assert len(session.added) == 1
