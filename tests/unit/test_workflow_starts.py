from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID

import pytest
from temporalio.exceptions import WorkflowAlreadyStartedError

from agents_should_survive_failure.persistence.models import (
    RunStatus,
    WorkflowRun,
    WorkflowStartAttempt,
    WorkflowStartStatus,
)
from agents_should_survive_failure.persistence.session import Database
from agents_should_survive_failure.workflow_starts import (
    RequestFingerprintConflict,
    TemporalWorkflowClient,
    WorkflowStartCoordinator,
    WorkflowStartUnavailable,
    classify_start_error,
    onboarding_request_fingerprint,
)


class ScalarResult:
    def __init__(self, value: UUID | None) -> None:
        self._value = value

    def scalar_one_or_none(self) -> UUID | None:
        return self._value


class ScalarsResult:
    def __init__(self, values: list[UUID]) -> None:
        self._values = values

    def all(self) -> list[UUID]:
        return self._values


class FakeSession:
    def __init__(self) -> None:
        self.run: WorkflowRun | None = None
        self.attempt: WorkflowStartAttempt | None = None
        self.inserted = True
        self.scalar_values: list[object | None] = []
        self.run_ids: list[UUID] = []

    async def execute(self, statement: Any) -> ScalarResult:
        if not self.inserted:
            return ScalarResult(None)
        params = statement.compile().params
        self.run = WorkflowRun(
            id=cast(UUID, params["id"]),
            agent_id=cast(UUID, params["agent_id"]),
            vendor_id=cast(UUID, params["vendor_id"]),
            requested_by_id=cast(UUID, params["requested_by_id"]),
            workflow_type=cast(str, params["workflow_type"]),
            temporal_workflow_id=cast(str, params["temporal_workflow_id"]),
            idempotency_key=cast(str, params["idempotency_key"]),
            request_fingerprint=cast(str, params["request_fingerprint"]),
            status=RunStatus.PENDING,
            input_summary={"vendor_id": str(params["vendor_id"])},
        )
        return ScalarResult(self.run.id)

    async def get(self, model: object, identifier: UUID) -> WorkflowRun | None:
        del model
        assert self.run is not None and identifier == self.run.id
        return self.run

    async def scalar(self, statement: object) -> object | None:
        del statement
        return self.scalar_values.pop(0)

    async def scalars(self, statement: object) -> ScalarsResult:
        del statement
        return ScalarsResult(self.run_ids)

    def add(self, value: WorkflowStartAttempt) -> None:
        self.attempt = value


class FakeDatabase:
    def __init__(self, session: FakeSession) -> None:
        self._session = session

    @asynccontextmanager
    async def session(self):  # type: ignore[no-untyped-def]
        yield self._session


class ScriptedTemporalClient:
    def __init__(self, outcomes: list[Exception | None]) -> None:
        self._outcomes = outcomes
        self.workflow_ids: list[str] = []

    async def start_workflow(
        self, workflow: object, arg: object, *, id: str, task_queue: str
    ) -> None:
        del workflow, arg, task_queue
        self.workflow_ids.append(id)
        outcome = self._outcomes.pop(0)
        if outcome is not None:
            raise outcome


def coordinator(
    session: FakeSession, temporal: ScriptedTemporalClient, now: datetime
) -> WorkflowStartCoordinator:
    return WorkflowStartCoordinator(
        cast(Database, FakeDatabase(session)),
        cast(TemporalWorkflowClient, temporal),
        now=lambda: now,
    )


@pytest.mark.asyncio
async def test_new_start_timeout_then_already_started_reconciles() -> None:
    session = FakeSession()
    now = datetime(2026, 7, 12, tzinfo=UTC)
    temporal = ScriptedTemporalClient(
        [TimeoutError(), WorkflowAlreadyStartedError("ignored", "vendor_onboarding")]
    )
    subject = coordinator(session, temporal, now)
    vendor_id = UUID("00000000-0000-0000-0000-000000000001")
    principal_id = UUID("00000000-0000-0000-0000-000000000002")
    agent_id = UUID("00000000-0000-0000-0000-000000000003")

    run = await subject.create_or_get(
        vendor_id=vendor_id,
        requested_by_id=principal_id,
        agent_id=agent_id,
        idempotency_key="replay-key",
    )
    assert session.attempt is not None
    assert run.temporal_workflow_id == f"vendor-onboarding-{run.id}"

    session.scalar_values = [session.attempt, session.attempt]
    with pytest.raises(WorkflowStartUnavailable) as unavailable:
        await subject.start(run.id)
    assert unavailable.value.category == "timeout"
    assert session.attempt.status is WorkflowStartStatus.FAILED
    assert session.attempt.attempts == 1

    session.scalar_values = [session.attempt, session.attempt]
    assert await subject.start(run.id) is run
    assert session.attempt.status is WorkflowStartStatus.STARTED
    assert session.attempt.attempts == 2
    assert temporal.workflow_ids == [run.temporal_workflow_id, run.temporal_workflow_id]


@pytest.mark.asyncio
async def test_replay_conflict_and_active_lease_do_not_start_again() -> None:
    session = FakeSession()
    now = datetime(2026, 7, 12, tzinfo=UTC)
    temporal = ScriptedTemporalClient([None])
    subject = coordinator(session, temporal, now)
    vendor_id = UUID("00000000-0000-0000-0000-000000000001")
    principal_id = UUID("00000000-0000-0000-0000-000000000002")
    agent_id = UUID("00000000-0000-0000-0000-000000000003")
    run = await subject.create_or_get(
        vendor_id=vendor_id,
        requested_by_id=principal_id,
        agent_id=agent_id,
        idempotency_key="replay-key",
    )
    assert session.attempt is not None

    session.inserted = False
    session.scalar_values = [run]
    assert (
        await subject.create_or_get(
            vendor_id=vendor_id,
            requested_by_id=principal_id,
            agent_id=agent_id,
            idempotency_key="replay-key",
        )
        is run
    )
    session.scalar_values = [run]
    with pytest.raises(RequestFingerprintConflict):
        await subject.create_or_get(
            vendor_id=UUID("00000000-0000-0000-0000-000000000004"),
            requested_by_id=principal_id,
            agent_id=agent_id,
            idempotency_key="replay-key",
        )

    session.attempt.lease_expires_at = now + timedelta(seconds=1)
    session.scalar_values = [session.attempt]
    assert await subject.start(run.id) is run
    assert temporal.workflow_ids == []


def test_fingerprint_is_canonical_and_start_errors_are_safe_categories() -> None:
    vendor_id = UUID("00000000-0000-0000-0000-000000000001")
    agent_id = UUID("00000000-0000-0000-0000-000000000003")

    assert onboarding_request_fingerprint(vendor_id=vendor_id, agent_id=agent_id) == (
        onboarding_request_fingerprint(vendor_id=vendor_id, agent_id=agent_id)
    )
    assert classify_start_error(TimeoutError()) == "timeout"
    assert classify_start_error(ConnectionError()) == "unavailable"
    assert classify_start_error(ValueError()) == "unexpected"


@pytest.mark.asyncio
async def test_recovery_retries_persisted_intents_and_reports_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = FakeSession()
    temporal = ScriptedTemporalClient([])
    subject = coordinator(session, temporal, datetime(2026, 7, 12, tzinfo=UTC))
    first = UUID("00000000-0000-0000-0000-000000000010")
    second = UUID("00000000-0000-0000-0000-000000000011")
    session.run_ids = [first, second]
    calls: list[UUID] = []

    async def start(run_id: UUID) -> WorkflowRun:
        calls.append(run_id)
        if run_id == second:
            raise WorkflowStartUnavailable("unavailable")
        return cast(WorkflowRun, object())

    monkeypatch.setattr(subject, "start", start)
    result = await subject.recover(limit=10)

    assert calls == [first, second]
    assert result.inspected == 2
    assert result.unavailable == 1
