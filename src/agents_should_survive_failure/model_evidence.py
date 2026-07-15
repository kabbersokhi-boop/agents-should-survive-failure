"""Persist provider call evidence without storing private reasoning."""

import time
import uuid

from opentelemetry import trace
from sqlalchemy.ext.asyncio import AsyncSession

from agents_should_survive_failure.metrics import (
    MODEL_CALLS,
    MODEL_LATENCY,
    MODEL_TOKENS,
)
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
        tracer = trace.get_tracer(__name__)
        try:
            # Prompts are intentionally not trace attributes or metric labels.
            with tracer.start_as_current_span("agents.model.call") as span:
                response = await self._provider.explain(ModelRequest(correlation_id, prompt))
                span.set_attribute("gen_ai.provider.name", response.provider)
                span.set_attribute("gen_ai.request.model", response.model)
        except Exception as error:
            latency = time.perf_counter() - started
            MODEL_CALLS.labels("unknown", "unknown", "failed").inc()
            MODEL_LATENCY.labels("unknown", "unknown", "failed").observe(latency)
            session.add(
                ModelCall(
                    workflow_run_id=workflow_run_id,
                    provider="unknown",
                    model="unknown",
                    correlation_id=correlation_id,
                    status=InvocationStatus.FAILED,
                    latency_ms=int(latency * 1000),
                    error_category=(
                        error.category if isinstance(error, ProviderError) else "provider_error"
                    ),
                )
            )
            raise
        latency = time.perf_counter() - started
        MODEL_CALLS.labels(response.provider, response.model, "succeeded").inc()
        MODEL_LATENCY.labels(response.provider, response.model, "succeeded").observe(latency)
        MODEL_TOKENS.labels(response.provider, response.model, "input").inc(response.input_tokens)
        MODEL_TOKENS.labels(response.provider, response.model, "output").inc(response.output_tokens)
        session.add(
            ModelCall(
                workflow_run_id=workflow_run_id,
                provider=response.provider,
                model=response.model,
                correlation_id=correlation_id,
                status=InvocationStatus.SUCCEEDED,
                input_tokens=response.input_tokens,
                output_tokens=response.output_tokens,
                latency_ms=int(latency * 1000),
                decision_summary=response.summary[: self._max_summary_characters],
            )
        )
        return response
