"""Policy retrieval and reindexing over the application-owned pgvector index."""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agents_should_survive_failure.persistence.models import PolicyDocument
from agents_should_survive_failure.providers import (
    DeterministicEmbeddingProvider,
    EmbeddingProvider,
)


@dataclass(frozen=True)
class PolicyCitation:
    document_id: str
    title: str
    source_uri: str
    content: str


class PolicyRetriever:
    def __init__(self, embedding_provider: EmbeddingProvider | None = None) -> None:
        self._embedding_provider = embedding_provider or DeterministicEmbeddingProvider()

    async def retrieve(
        self, session: AsyncSession, query: str, *, limit: int = 3
    ) -> list[PolicyCitation]:
        embedding = (await self._embedding_provider.embed(query, input_type="query")).vector
        documents = await session.scalars(
            select(PolicyDocument)
            .order_by(PolicyDocument.embedding.cosine_distance(embedding))
            .limit(limit)
        )
        return [
            PolicyCitation(str(item.id), item.title, item.source_uri, item.content)
            for item in documents.all()
        ]


class PolicyEmbeddingService:
    def __init__(self, embedding_provider: EmbeddingProvider) -> None:
        self._embedding_provider = embedding_provider

    async def reindex_all(self, session: AsyncSession) -> int:
        documents = (await session.scalars(select(PolicyDocument))).all()
        for document in documents:
            response = await self._embedding_provider.embed(document.content, input_type="passage")
            document.embedding_model = response.model
            document.embedding = response.vector
        return len(documents)
