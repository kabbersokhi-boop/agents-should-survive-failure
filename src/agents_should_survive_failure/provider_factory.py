"""Explicit provider selection for runtime processes."""

from agents_should_survive_failure.providers import (
    DeterministicEmbeddingProvider,
    DeterministicModelProvider,
    EmbeddingProvider,
    ModelProvider,
    NVIDIAEmbeddingProvider,
    NVIDIAModelProvider,
)
from agents_should_survive_failure.settings import Settings


def build_model_provider(settings: Settings) -> ModelProvider:
    if settings.model_provider == "deterministic":
        return DeterministicModelProvider()
    if settings.model_provider == "nvidia_nim":
        return NVIDIAModelProvider(
            api_key=settings.nvidia_api_key,
            base_url=settings.nvidia_base_url,
            model=settings.nvidia_model,
            max_output_tokens=settings.model_max_output_tokens,
        )
    raise ValueError(f"Unsupported MODEL_PROVIDER: {settings.model_provider}")


def build_embedding_provider(settings: Settings) -> EmbeddingProvider:
    if settings.model_provider == "deterministic":
        return DeterministicEmbeddingProvider()
    if settings.model_provider == "nvidia_nim":
        return NVIDIAEmbeddingProvider(
            api_key=settings.nvidia_api_key,
            base_url=settings.nvidia_base_url,
            model=settings.nvidia_embedding_model,
        )
    raise ValueError(f"Unsupported MODEL_PROVIDER: {settings.model_provider}")
