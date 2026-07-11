"""Deterministic policy retrieval over the application-owned pgvector index."""

import hashlib
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agents_should_survive_failure.persistence.models import PolicyDocument


def deterministic_embedding(text: str) -> list[float]:
    """Create a stable test-only eight-dimensional embedding without a provider call."""
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    values = [float(byte) / 255 for byte in digest[:8]]
    magnitude = sum(value * value for value in values) ** 0.5
    return [value / magnitude for value in values] if magnitude else values


@dataclass(frozen=True)
class PolicyCitation:
    document_id: str
    title: str
    source_uri: str
    content: str


class PolicyRetriever:
    async def retrieve(
        self, session: AsyncSession, query: str, *, limit: int = 3
    ) -> list[PolicyCitation]:
        embedding = deterministic_embedding(query)
        documents = await session.scalars(
            select(PolicyDocument)
            .order_by(PolicyDocument.embedding.cosine_distance(embedding))
            .limit(limit)
        )
        return [
            PolicyCitation(str(item.id), item.title, item.source_uri, item.content)
            for item in documents.all()
        ]
