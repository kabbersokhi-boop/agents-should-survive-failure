from email.message import Message
from urllib.error import HTTPError

import pytest

from agents_should_survive_failure.providers import (
    DeterministicModelProvider,
    ModelRequest,
    NVIDIAEmbeddingProvider,
    NVIDIAModelProvider,
    ProviderError,
    classify_provider_error,
)


@pytest.mark.asyncio
async def test_deterministic_provider_returns_bounded_explanation() -> None:
    response = await DeterministicModelProvider().explain(
        ModelRequest(correlation_id="test", prompt="Vendor is low risk")
    )

    assert response.provider == "deterministic_mock"
    assert response.input_tokens == 4
    assert "reasoning" not in response.summary.lower()


def test_nvidia_provider_requires_explicit_credential() -> None:
    with pytest.raises(ValueError, match="NVIDIA_API_KEY"):
        NVIDIAModelProvider(api_key=None, base_url="https://example.invalid/v1", model="demo")


@pytest.mark.asyncio
async def test_nvidia_embedding_provider_validates_vector_dimension(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = NVIDIAEmbeddingProvider(
        api_key="test-key",
        base_url="https://example.invalid/v1",
        model="nvidia/llama-nemotron-embed-1b-v2",
    )

    def post(payload: dict[str, object]) -> dict[str, object]:
        del payload
        return {"data": [{"embedding": [0.5] * 2048}]}

    monkeypatch.setattr(provider, "_post", post)

    response = await provider.embed("vendor approval", input_type="query")

    assert response.provider == "nvidia_nim"
    assert len(response.vector) == 2048


@pytest.mark.asyncio
async def test_nvidia_model_provider_parses_completion(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = NVIDIAModelProvider(
        api_key="test-key",
        base_url="https://example.invalid/v1",
        model="mistralai/mistral-medium-3.5-128b",
    )

    def post(payload: dict[str, object]) -> dict[str, object]:
        assert payload["max_tokens"] == 256
        return {
            "choices": [{"message": {"content": "OK"}}],
            "usage": {"prompt_tokens": 2, "completion_tokens": 1},
        }

    monkeypatch.setattr(provider, "_post", post)
    response = await provider.explain(ModelRequest(correlation_id="test", prompt="Reply."))

    assert response.summary == "OK"
    assert response.input_tokens == 2


@pytest.mark.parametrize(
    ("status_code", "category"),
    [(401, "authentication"), (429, "rate_limit"), (404, "unavailable_model"), (500, "timeout")],
)
def test_nvidia_http_errors_have_safe_categories(status_code: int, category: str) -> None:
    error = HTTPError("https://example.invalid", status_code, "failure", Message(), None)

    classified = classify_provider_error(error)

    assert isinstance(classified, ProviderError)
    assert classified.category == category
