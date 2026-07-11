"""Provider-independent model contracts with deterministic local behavior."""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ModelRequest:
    correlation_id: str
    prompt: str


@dataclass(frozen=True)
class ModelResponse:
    provider: str
    model: str
    summary: str
    input_tokens: int
    output_tokens: int


class ModelProvider(Protocol):
    async def explain(self, request: ModelRequest) -> ModelResponse: ...


class DeterministicModelProvider:
    """Stable provider used by local runs and CI; it cannot authorize decisions."""

    provider_name = "deterministic_mock"
    model_name = "deterministic-explainer-v1"

    async def explain(self, request: ModelRequest) -> ModelResponse:
        words = request.prompt.split()
        return ModelResponse(
            provider=self.provider_name,
            model=self.model_name,
            summary="Deterministic explanation based only on supplied evidence.",
            input_tokens=len(words),
            output_tokens=8,
        )
