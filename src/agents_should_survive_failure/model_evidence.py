"""Persist provider call evidence without storing private reasoning."""

import time
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from agents_should_survive_failure.persistence.models import InvocationStatus, ModelCall
from agents_should_survive_failure.providers import (
    ModelProvider,
    ModelRequest,
    ModelResponse,
    ProviderError,
)


class ModelEvidenceService:
    def __init__(self, provider: ModelProvider, *, max_summary_characters: int = 1000) -> None:
        self._provider = provider
        self._max_summary_characters = max_summary_characters

    async def explain(
        self, session: AsyncSession, *, workflow_run_id: uuid.UUID, prompt: str, correlation_id: str
    ) -> ModelResponse:
        started = time.perf_counter()
        try:
            response = await self._provider.explain(ModelRequest(correlation_id, prompt))
        except Exception as error:
            session.add(
                ModelCall(
                    workflow_run_id=workflow_run_id,
                    provider="unknown",
                    model="unknown",
                    correlation_id=correlation_id,
                    status=InvocationStatus.FAILED,
                    latency_ms=int((time.perf_counter() - started) * 1000),
                    error_category=(
                        error.category if isinstance(error, ProviderError) else "provider_error"
                    ),
                )
            )
            raise
        session.add(
            ModelCall(
                workflow_run_id=workflow_run_id,
                provider=response.provider,
                model=response.model,
                correlation_id=correlation_id,
                status=InvocationStatus.SUCCEEDED,
                input_tokens=response.input_tokens,
                output_tokens=response.output_tokens,
                latency_ms=int((time.perf_counter() - started) * 1000),
                decision_summary=response.summary[: self._max_summary_characters],
            )
        )
        return response
