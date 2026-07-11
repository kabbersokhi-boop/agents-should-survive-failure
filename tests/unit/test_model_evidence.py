import uuid
from typing import cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from agents_should_survive_failure.model_evidence import ModelEvidenceService
from agents_should_survive_failure.persistence.models import ModelCall
from agents_should_survive_failure.providers import DeterministicModelProvider, ProviderError


class FakeSession:
    def __init__(self) -> None:
        self.added: list[object] = []

    def add(self, item: object) -> None:
        self.added.append(item)


@pytest.mark.asyncio
async def test_model_evidence_records_only_summary_and_usage() -> None:
    session = FakeSession()
    response = await ModelEvidenceService(DeterministicModelProvider()).explain(
        cast(AsyncSession, session),
        workflow_run_id=uuid.uuid4(),
        prompt="Evidence for a vendor review",
        correlation_id="correlation-1",
    )

    assert response.input_tokens == 5
    assert len(session.added) == 1


@pytest.mark.asyncio
async def test_model_evidence_bounds_stored_summary() -> None:
    class VerboseProvider:
        async def explain(self, request: object):  # type: ignore[no-untyped-def]
            del request
            from agents_should_survive_failure.providers import ModelResponse

            return ModelResponse("test", "verbose", "x" * 20, 1, 1)

    session = FakeSession()
    await ModelEvidenceService(VerboseProvider(), max_summary_characters=10).explain(  # type: ignore[arg-type]
        cast(AsyncSession, session),
        workflow_run_id=uuid.uuid4(),
        prompt="test",
        correlation_id="bounded",
    )
    stored = cast(ModelCall, session.added[0])
    assert stored.decision_summary == "x" * 10


@pytest.mark.asyncio
async def test_model_evidence_records_safe_provider_error_category() -> None:
    class FailingProvider:
        async def explain(self, request: object):  # type: ignore[no-untyped-def]
            del request
            raise ProviderError("rate_limit", "provider rate limit exceeded")

    session = FakeSession()
    with pytest.raises(ProviderError):
        await ModelEvidenceService(FailingProvider()).explain(  # type: ignore[arg-type]
            cast(AsyncSession, session),
            workflow_run_id=uuid.uuid4(),
            prompt="test",
            correlation_id="rate-limit",
        )

    stored = cast(ModelCall, session.added[0])
    assert stored.error_category == "rate_limit"
