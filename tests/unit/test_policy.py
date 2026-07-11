from typing import cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from agents_should_survive_failure.policy import PolicyEmbeddingService, PolicyRetriever
from agents_should_survive_failure.providers import (
    DeterministicEmbeddingProvider,
    EmbeddingInputType,
    EmbeddingResponse,
)


class FakeDocument:
    def __init__(self) -> None:
        self.id = "document-id"
        self.title = "Vendor policy"
        self.source_uri = "test://policy"
        self.content = "Vendors require approval."
        self.embedding_model = "old-model"
        self.embedding: list[float] = []


class FakeScalarResult:
    def __init__(self, documents: list[FakeDocument]) -> None:
        self._documents = documents

    def all(self) -> list[FakeDocument]:
        return self._documents


class FakeSession:
    def __init__(self, documents: list[FakeDocument]) -> None:
        self._documents = documents

    async def scalars(self, statement: object) -> FakeScalarResult:
        del statement
        return FakeScalarResult(self._documents)


class FakeEmbeddingProvider:
    async def embed(self, text: str, *, input_type: EmbeddingInputType) -> EmbeddingResponse:
        del text
        return EmbeddingResponse(
            provider="test",
            model=f"test-{input_type}",
            vector=[0.5] * 2048,
        )


@pytest.mark.asyncio
async def test_deterministic_embedding_is_stable_and_normalized() -> None:
    provider = DeterministicEmbeddingProvider()
    first = await provider.embed("vendor approval policy", input_type="query")
    second = await provider.embed("vendor approval policy", input_type="query")

    assert first == second
    assert len(first.vector) == 2048
    assert round(sum(value * value for value in first.vector), 8) == 1.0


@pytest.mark.asyncio
async def test_policy_retriever_and_reindex_service_use_correct_embedding_modes() -> None:
    document = FakeDocument()
    session = FakeSession([document])
    provider = FakeEmbeddingProvider()

    async_session = cast(AsyncSession, session)
    citations = await PolicyRetriever(provider).retrieve(async_session, "vendor approval")
    count = await PolicyEmbeddingService(provider).reindex_all(async_session)

    assert citations[0].title == "Vendor policy"
    assert count == 1
    assert document.embedding_model == "test-passage"
    assert len(document.embedding) == 2048
