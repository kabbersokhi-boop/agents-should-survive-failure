import pytest

from agents_should_survive_failure.provider_factory import (
    build_embedding_provider,
    build_model_provider,
)
from agents_should_survive_failure.providers import (
    DeterministicEmbeddingProvider,
    DeterministicModelProvider,
    NVIDIAEmbeddingProvider,
    NVIDIAModelProvider,
)
from agents_should_survive_failure.settings import Settings


def test_factory_selects_deterministic_providers_by_default() -> None:
    settings = Settings(model_provider="deterministic")

    assert isinstance(build_model_provider(settings), DeterministicModelProvider)
    assert isinstance(build_embedding_provider(settings), DeterministicEmbeddingProvider)


def test_factory_selects_nvidia_providers_with_explicit_configuration() -> None:
    settings = Settings(model_provider="nvidia_nim", nvidia_api_key="test-key")

    assert isinstance(build_model_provider(settings), NVIDIAModelProvider)
    assert isinstance(build_embedding_provider(settings), NVIDIAEmbeddingProvider)


def test_factory_rejects_unknown_provider() -> None:
    settings = Settings(model_provider="unknown")

    with pytest.raises(ValueError, match="Unsupported MODEL_PROVIDER"):
        build_model_provider(settings)
    with pytest.raises(ValueError, match="Unsupported MODEL_PROVIDER"):
        build_embedding_provider(settings)
