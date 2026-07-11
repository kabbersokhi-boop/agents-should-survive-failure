import uuid
from typing import cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from agents_should_survive_failure.model_evidence import ModelEvidenceService
from agents_should_survive_failure.providers import DeterministicModelProvider


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
