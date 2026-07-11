from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast

import pytest

from agents_should_survive_failure.persistence.models import RunStatus, VendorStatus
from agents_should_survive_failure.persistence.repositories import WorkflowRunRepository
from agents_should_survive_failure.persistence.session import Database
from agents_should_survive_failure.workflows import activities as activity_module
from agents_should_survive_failure.workflows.contracts import VendorOnboardingInput


class FakeSession:
    def __init__(self) -> None:
        self.added: list[object] = []

    async def __aenter__(self) -> "FakeSession":
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    def add(self, item: object) -> None:
        self.added.append(item)

    async def flush(self) -> None:
        return None


class FakeDatabase:
    def __init__(self, session: FakeSession) -> None:
        self._session = session

    def session(self) -> FakeSession:
        return self._session


@pytest.fixture
def activity_info(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        activity_module.activity,
        "info",
        lambda: SimpleNamespace(started_time=datetime(2026, 7, 11, tzinfo=UTC)),
    )


@pytest.mark.asyncio
async def test_begin_review_transitions_submitted_vendor(
    monkeypatch: pytest.MonkeyPatch, activity_info: None
) -> None:
    session = FakeSession()
    run = SimpleNamespace(status=RunStatus.PENDING, started_at=None)
    vendor = SimpleNamespace(status=VendorStatus.SUBMITTED)

    class Runs:
        async def get(self, run_id: object) -> object:
            return run

    class Vendors:
        async def get(self, vendor_id: object, *, for_update: bool = False) -> object:
            assert for_update
            return vendor

    events: list[str] = []
    audits: list[str] = []

    def make_runs(_: object) -> Runs:
        return Runs()

    def make_vendors(_: object) -> Vendors:
        return Vendors()

    monkeypatch.setattr(activity_module, "WorkflowRunRepository", make_runs)
    monkeypatch.setattr(activity_module, "VendorRepository", make_vendors)
    onboarding = activity_module.VendorOnboardingActivities(cast(Database, FakeDatabase(session)))

    async def append(*args: object, **kwargs: object) -> None:
        events.append(str(args[2]))

    async def audit(*args: object, **kwargs: object) -> None:
        audits.append(str(args[2]))

    monkeypatch.setattr(onboarding, "_append_event", append)
    monkeypatch.setattr(onboarding, "_audit", audit)

    await onboarding.begin_review(
        VendorOnboardingInput(
            run_id="00000000-0000-0000-0000-000000000010",
            vendor_id="00000000-0000-0000-0000-000000000020",
        )
    )

    assert run.status is RunStatus.RUNNING
    assert vendor.status is VendorStatus.UNDER_REVIEW
    assert events == ["review.started"]
    assert audits == ["vendor.review.start"]


@pytest.mark.asyncio
async def test_assess_risk_uses_deterministic_jurisdiction_rule(
    monkeypatch: pytest.MonkeyPatch, activity_info: None
) -> None:
    session = FakeSession()
    vendor = SimpleNamespace(jurisdiction="ZZ", risk_score=None)

    class Vendors:
        async def get(self, vendor_id: object, *, for_update: bool = False) -> object:
            assert for_update
            return vendor

    def make_vendors(_: object) -> Vendors:
        return Vendors()

    def make_runs(_: object) -> object:
        return object()

    onboarding = activity_module.VendorOnboardingActivities(cast(Database, FakeDatabase(session)))
    monkeypatch.setattr(activity_module, "VendorRepository", make_vendors)
    monkeypatch.setattr(activity_module, "WorkflowRunRepository", make_runs)
    monkeypatch.setattr(onboarding, "_append_event", _done)
    monkeypatch.setattr(onboarding, "_audit", _done)

    result = await onboarding.assess_risk(
        VendorOnboardingInput(
            run_id="00000000-0000-0000-0000-000000000010",
            vendor_id="00000000-0000-0000-0000-000000000020",
        )
    )

    assert result.score == 65
    assert vendor.risk_score == 65


@pytest.mark.asyncio
async def test_cancel_review_is_idempotent_for_terminal_run(
    monkeypatch: pytest.MonkeyPatch, activity_info: None
) -> None:
    session = FakeSession()
    run = SimpleNamespace(status=RunStatus.RUNNING, completed_at=None)

    class Runs:
        async def get(self, run_id: object) -> object:
            return run

    def make_runs(_: object) -> Runs:
        return Runs()

    onboarding = activity_module.VendorOnboardingActivities(cast(Database, FakeDatabase(session)))
    monkeypatch.setattr(activity_module, "WorkflowRunRepository", make_runs)
    monkeypatch.setattr(onboarding, "_append_event", _done)
    monkeypatch.setattr(onboarding, "_audit", _done)

    await onboarding.cancel_review(
        VendorOnboardingInput(
            run_id="00000000-0000-0000-0000-000000000010",
            vendor_id="00000000-0000-0000-0000-000000000020",
        )
    )

    assert run.status is RunStatus.CANCELLED


@pytest.mark.asyncio
async def test_append_event_skips_existing_sequence() -> None:
    class Runs:
        def __init__(self) -> None:
            self.appended: list[object] = []

        async def events(self, run_id: object) -> list[object]:
            return [SimpleNamespace(sequence=10)]

        async def append_event(self, event: object) -> None:
            self.appended.append(event)

    runs = Runs()
    onboarding = activity_module.VendorOnboardingActivities(
        cast(Database, FakeDatabase(FakeSession()))
    )
    await onboarding._append_event(  # pyright: ignore[reportPrivateUsage]
        cast(WorkflowRunRepository, runs),
        activity_module.uuid.UUID("00000000-0000-0000-0000-000000000010"),
        "review.started",
        "Review started.",
        {},
        sequence=10,
    )

    assert runs.appended == []


async def _done(*args: object, **kwargs: object) -> None:
    return None
