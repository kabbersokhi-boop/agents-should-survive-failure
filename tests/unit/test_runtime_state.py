"""Durable runtime-state validation and deterministic budget accounting checks."""

from types import SimpleNamespace
from typing import cast
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from agents_should_survive_failure.failures import FailureCategory, PlatformFailure
from agents_should_survive_failure.persistence.models import RunArtifact, RunBudget
from agents_should_survive_failure.runtime_state import (
    RuntimeStateValidationError,
    consume_budget,
    create_artifact,
    read_artifact,
    save_checkpoint,
)

RUN_ID = UUID("00000000-0000-0000-0000-000000000001")
AGENT_ID = UUID("00000000-0000-0000-0000-000000000002")


class FakeSession:
    def __init__(
        self, *, run: object | None, scalar_values: list[object | None] | None = None
    ) -> None:
        self._run = run
        self._scalar_values = scalar_values or []
        self.added: list[object] = []

    async def get(self, model: object, identifier: object) -> object | None:
        del model, identifier
        return self._run

    async def scalar(self, statement: object) -> object | None:
        del statement
        return self._scalar_values.pop(0) if self._scalar_values else None

    def add(self, value: object) -> None:
        self.added.append(value)

    async def flush(self) -> None:
        return None


@pytest.mark.asyncio
async def test_checkpoint_is_bound_to_the_pinned_agent_and_sized_before_persistence() -> None:
    session = FakeSession(run=SimpleNamespace(agent_id=AGENT_ID))

    checkpoint = await save_checkpoint(
        cast(AsyncSession, session),
        workflow_run_id=RUN_ID,
        agent_id=AGENT_ID,
        name="investigation-state",
        schema_version="1",
        value={"cursor": 2},
        maximum_bytes=100,
    )

    assert checkpoint.agent_id == AGENT_ID
    assert checkpoint.size_bytes > 0
    assert session.added == [checkpoint]


@pytest.mark.asyncio
async def test_artifact_rejects_path_traversal_before_database_access() -> None:
    with pytest.raises(RuntimeStateValidationError, match="artifact name"):
        await create_artifact(
            cast(AsyncSession, FakeSession(run=None)),
            workflow_run_id=RUN_ID,
            agent_id=AGENT_ID,
            name="../escape.txt",
            content_type="text/plain",
            content=b"nope",
            maximum_bytes=100,
        )


@pytest.mark.asyncio
async def test_budget_rejects_exhaustion_without_incrementing_usage() -> None:
    budget = RunBudget(workflow_run_id=RUN_ID, limits={"tool_calls": 1}, consumed={"tool_calls": 1})
    session = FakeSession(run=None, scalar_values=[budget])

    with pytest.raises(PlatformFailure) as raised:
        await consume_budget(
            cast(AsyncSession, session), workflow_run_id=RUN_ID, amount={"tool_calls": 1}
        )

    assert raised.value.category is FailureCategory.BUDGET_EXHAUSTED
    assert budget.consumed == {"tool_calls": 1}


@pytest.mark.asyncio
async def test_artifact_read_rejects_tampered_inline_bytes() -> None:
    artifact = RunArtifact(
        id=UUID("00000000-0000-0000-0000-000000000003"),
        workflow_run_id=RUN_ID,
        agent_id=AGENT_ID,
        parent_artifact_id=None,
        name="investigation.json",
        content_type="application/json",
        digest_sha256="0" * 64,
        size_bytes=2,
        content=b"{}",
    )

    class ArtifactSession:
        async def get(self, model: object, identifier: object) -> object | None:
            del model, identifier
            return artifact

    with pytest.raises(RuntimeStateValidationError, match="integrity"):
        await read_artifact(
            cast(AsyncSession, ArtifactSession()),
            workflow_run_id=RUN_ID,
            agent_id=AGENT_ID,
            artifact_id=artifact.id,
        )
