"""Policy retrieval and reindexing over the application-owned pgvector index."""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agents_should_survive_failure.persistence.models import PolicyDocument
from agents_should_survive_failure.providers import (
    DeterministicEmbeddingProvider,
    EmbeddingProvider,
)

# Tool grants belong to the platform, never to agent-provided call arguments or mutable agent
# configuration. New registered agent versions require an explicit policy review and entry here.
_AGENT_TOOL_POLICIES: dict[tuple[str, str], frozenset[str]] = {
    ("vendor-onboarding", "1"): frozenset({"vendors:read", "policy:read", "email:send"}),
}


def agent_tool_permissions(*, name: str, version: str) -> frozenset[str]:
    """Return the immutable platform grants for one registered agent version."""
    return _AGENT_TOOL_POLICIES.get((name, version), frozenset())


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
