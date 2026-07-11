from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast

import pytest

from agents_should_survive_failure.persistence.models import (
    ApprovalStatus,
    InvocationStatus,
    ModelCall,
    RunStatus,
    VendorStatus,
)
from agents_should_survive_failure.persistence.repositories import WorkflowRunRepository
from agents_should_survive_failure.persistence.session import Database
from agents_should_survive_failure.workflows import activities as activity_module
from agents_should_survive_failure.workflows.contracts import (
    ApprovalDecisionInput,
    ApprovalDecisionType,
    VendorOnboardingInput,
)


class FakeSession:
    def __init__(self) -> None:
        self.added: list[object] = []
        self.scalar_values: list[object | None] = []

    async def __aenter__(self) -> "FakeSession":
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    def add(self, item: object) -> None:
        self.added.append(item)

    async def flush(self) -> None:
        return None

    async def scalar(self, statement: object) -> object | None:
        del statement
        return self.scalar_values.pop(0) if self.scalar_values else None


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

    class Retriever:
        async def retrieve(self, session: object, query: str) -> list[object]:
            del session, query
            return []

    onboarding = activity_module.VendorOnboardingActivities(
        cast(Database, FakeDatabase(session)),
        policy_retriever=cast(activity_module.PolicyRetriever, Retriever()),
    )
    monkeypatch.setattr(activity_module, "VendorRepository", make_vendors)
    monkeypatch.setattr(activity_module, "WorkflowRunRepository", make_runs)
    events: list[tuple[str, dict[str, object]]] = []

    async def append(*args: object, **kwargs: object) -> None:
        del kwargs
        events.append((str(args[2]), cast(dict[str, object], args[4])))

    monkeypatch.setattr(onboarding, "_append_event", append)
    monkeypatch.setattr(onboarding, "_audit", _done)

    result = await onboarding.assess_risk(
        VendorOnboardingInput(
            run_id="00000000-0000-0000-0000-000000000010",
            vendor_id="00000000-0000-0000-0000-000000000020",
        )
    )

    assert result.score == 65
    assert vendor.risk_score == 65
    assert events[1] == (
        "risk.policy_context",
        {"citations": [], "model_explanation_available": True},
    )


def test_risk_explanation_prompt_preserves_deterministic_authority() -> None:
    prompt = activity_module.VendorOnboardingActivities._risk_explanation_prompt(  # pyright: ignore[reportPrivateUsage]
        jurisdiction="US",
        score=25,
        policy_context="Human approval is required.",
    )

    assert "Do not recommend or authorize an approval decision." in prompt
    assert "Deterministic risk score: 25" in prompt


@pytest.mark.asyncio
async def test_assess_risk_records_policy_citations_when_model_is_unavailable(
    monkeypatch: pytest.MonkeyPatch, activity_info: None
) -> None:
    session = FakeSession()
    vendor = SimpleNamespace(jurisdiction="US", risk_score=None)

    class Vendors:
        async def get(self, vendor_id: object, *, for_update: bool = False) -> object:
            assert for_update
            return vendor

    class Retriever:
        async def retrieve(self, session: object, query: str) -> list[object]:
            del session
            assert query == "vendor onboarding approval policy"
            return [
                SimpleNamespace(
                    document_id="policy-1",
                    title="Vendor Approval Policy",
                    source_uri="policy://vendor-approval",
                    content="Human approval is required.",
                )
            ]

    class FailingModelProvider:
        async def explain(self, request: object) -> object:
            del request
            raise RuntimeError("provider unavailable")

    def make_vendors(session: object) -> Vendors:
        del session
        return Vendors()

    def make_runs(session: object) -> object:
        del session
        return object()

    monkeypatch.setattr(activity_module, "VendorRepository", make_vendors)
    monkeypatch.setattr(activity_module, "WorkflowRunRepository", make_runs)
    onboarding = activity_module.VendorOnboardingActivities(
        cast(Database, FakeDatabase(session)),
        model_provider=cast(activity_module.ModelProvider, FailingModelProvider()),
        policy_retriever=cast(activity_module.PolicyRetriever, Retriever()),
    )
    events: list[tuple[str, dict[str, object]]] = []

    async def append(*args: object, **kwargs: object) -> None:
        del kwargs
        events.append((str(args[2]), cast(dict[str, object], args[4])))

    monkeypatch.setattr(onboarding, "_append_event", append)
    monkeypatch.setattr(onboarding, "_audit", _done)

    assessment = await onboarding.assess_risk(
        VendorOnboardingInput(
            run_id="00000000-0000-0000-0000-000000000010",
            vendor_id="00000000-0000-0000-0000-000000000020",
        )
    )

    assert assessment.score == 25
    assert vendor.risk_score == 25
    assert events[1][1] == {
        "citations": [
            {
                "document_id": "policy-1",
                "title": "Vendor Approval Policy",
                "source_uri": "policy://vendor-approval",
            }
        ],
        "model_explanation_available": False,
    }
    model_call = next(item for item in session.added if isinstance(item, ModelCall))
    assert model_call.status is InvocationStatus.FAILED


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
async def test_record_decision_rejects_a_stale_or_cancelled_approval(
    monkeypatch: pytest.MonkeyPatch, activity_info: None
) -> None:
    session = FakeSession()
    request = SimpleNamespace(
        id="00000000-0000-0000-0000-000000000030",
        status=ApprovalStatus.CANCELLED,
        version=2,
    )
    session.scalar_values = [request, None]
    run = SimpleNamespace(status=RunStatus.WAITING, completed_at=None, result_summary=None)
    vendor = SimpleNamespace(status=VendorStatus.UNDER_REVIEW, risk_score=25)

    class Runs:
        async def get(self, run_id: object) -> object:
            del run_id
            return run

    class Vendors:
        async def get(self, vendor_id: object, *, for_update: bool = False) -> object:
            del vendor_id
            assert for_update
            return vendor

    def make_runs(_: object) -> Runs:
        return Runs()

    def make_vendors(_: object) -> Vendors:
        return Vendors()

    monkeypatch.setattr(activity_module, "WorkflowRunRepository", make_runs)
    monkeypatch.setattr(activity_module, "VendorRepository", make_vendors)
    onboarding = activity_module.VendorOnboardingActivities(cast(Database, FakeDatabase(session)))

    with pytest.raises(ValueError, match="no longer pending"):
        await onboarding.record_decision(
            VendorOnboardingInput(
                run_id="00000000-0000-0000-0000-000000000010",
                vendor_id="00000000-0000-0000-0000-000000000020",
            ),
            ApprovalDecisionInput(
                approval_request_id="00000000-0000-0000-0000-000000000030",
                expected_version=1,
                decision=ApprovalDecisionType.APPROVED,
                decided_by_id="00000000-0000-0000-0000-000000000040",
                rationale="Stale approval must not apply.",
                idempotency_key="stale-decision",
            ),
        )

    assert session.added == []
    assert run.status is RunStatus.WAITING
    assert vendor.status is VendorStatus.UNDER_REVIEW


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
